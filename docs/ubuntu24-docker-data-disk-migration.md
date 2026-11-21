# Ubuntu 24.04：为 `/var/lib/docker` 备份并迁移到独立磁盘

本文用于当前这台虚拟机：

- 系统：Ubuntu Server 24.04
- 根分区：约 30 GiB，已接近 100%
- 主要占用：Docker / OpenSandbox
- 当前风险：PostgreSQL 因空间/持久化问题处于 recovery，Redis RDB 保存失败并拒绝写入
- 目标：新增一块虚拟磁盘，完整保留现有 Docker 数据，将 `/var/lib/docker` 迁移到新盘

> **不要执行** `docker volume prune`、`docker system prune -a --volumes`，也不要直接删除
> `/var/lib/docker`。当前工作区卷可能包含未提交代码。

---

## 一、操作前必须知道的事

1. `opensandbox/code-interpreter:v1.1.0` 是基础镜像，10.5 GiB 左右属于正常体积，不要为了省空间
   直接删除正在使用的镜像。
2. Docker 命名卷里可能有：
   - `ai-coding-ws-<session_id>`：会话工作区；
   - `ai-coding-m2`：共享 Maven 缓存；
   - OpenSandbox / Compose 的其他业务卷。
   这些卷必须一起复制。
3. 复制期间必须停止 Docker，保证 `/var/lib/docker` 没有新写入。
4. 新盘建议 **80–100 GiB**。如果宿主机空间允许，100 GiB 更稳妥。
5. 迁移只解决 Docker 占用；PostgreSQL 和 Redis 数据仍在原根分区。Docker 移走后应释放出足够
   空间，让 PostgreSQL 完成 recovery、Redis 重新成功保存 RDB。
6. 当前 PostgreSQL 处于 recovery 时不能依赖 `pg_dump`。数据库逻辑备份应在 PostgreSQL
   恢复且校验通过后再做。
7. 如果宿主机有足够空间，迁移前创建 VMware snapshot；snapshot 不能代替本文的全量数据核对，
   并且在虚拟机磁盘接近满时也要先确认宿主机有额外空间。

---

## 二、先在 VMware 中新增虚拟磁盘

1. 在 VMware 中给虚拟机新增一块虚拟磁盘，建议 100 GiB，类型可选 SCSI 或 NVMe。
2. 不建议直接扩容根分区后混用；本次目标是把 Docker 数据彻底从 `/var` 所属根盘移出去。
3. 如果虚拟机不支持热添加磁盘，需要在 VMware 设置中新增后短暂停机；这属于业务停机操作，
   应先把正在运行的 Agent/OpenSandbox 任务结束。

热添加 SCSI 磁盘后，可在 Ubuntu 中触发重新扫描：

```bash
for host in /sys/class/scsi_host/host*/scan; do
  echo "- - -" | sudo tee "$host" >/dev/null
done

sudo lsblk -o NAME,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,MODEL,SERIAL
```

如果仍看不到新盘，使用 VMware 的磁盘重新扫描，或按计划停机后再继续。

---

## 三、识别新盘并记录当前状态

先执行并保存以下输出：

```bash
lsb_release -a
uname -a
df -hT
df -i
sudo lsblk -o NAME,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,MODEL,SERIAL
sudo docker info
sudo docker ps -a --no-trunc
sudo docker images --no-trunc
sudo docker volume ls
sudo docker system df -v
```

必须确认新盘符合以下全部条件，才可以格式化：

- 是刚刚新增的磁盘；
- 容量与 VMware 中新增的容量一致；
- 没有挂载点；
- `FSTYPE` 为空，确实是一块空盘。

**不要凭 `/dev/sdb` 这种名称直接操作。** 不同启动顺序可能导致设备名变化。

下文用 `/dev/NEW_DISK` 表示确认后的整块新盘，例如 `/dev/sdb`；实际执行时替换。

可以先做一次只读检查：

```bash
sudo lsblk -f /dev/NEW_DISK
sudo wipefs --no-act /dev/NEW_DISK
```

`wipefs --no-act` 只显示现有签名，不修改磁盘。如果它显示已有文件系统或分区表，立即停止。

---

## 四、根盘 100% 时先安全腾出一点操作空间

如果连创建临时挂载目录都失败，先做不涉及业务数据的最小清理：

```bash
sudo apt clean
sudo journalctl --vacuum-size=500M
```

不要执行以下操作：

```bash
# 不要
sudo rm -rf /var/lib/docker
sudo rm -rf /var/lib/postgresql
sudo rm -rf /var/lib/redis
sudo docker system prune -a --volumes
```

---

## 五、格式化新盘并挂到临时迁移目录

以下命令会清空 `/dev/NEW_DISK` 上的内容，执行前再次核对 `lsblk -f`。

```bash
sudo parted -s /dev/NEW_DISK mklabel gpt
sudo parted -s /dev/NEW_DISK unit MiB mkpart docker-data ext4 1MiB 100%

# 先确认新分区名称，例如 /dev/sdb1
sudo lsblk -f /dev/NEW_DISK
```

格式化：

```bash
sudo mkfs.ext4 -m 1 -L docker-data /dev/NEW_DISK_PARTITION
```

例如：

```bash
sudo mkfs.ext4 -m 1 -L docker-data /dev/sdb1
```

挂到临时目录：

```bash
sudo mkdir -p /mnt/docker-migrate
sudo mount /dev/NEW_DISK_PARTITION /mnt/docker-migrate
findmnt /mnt/docker-migrate
```

先写入一个迁移标记，确认新盘确实可写：

```bash
echo "docker migration $(date -Is)" | sudo tee /mnt/docker-migrate/.migration-start
```

这个标记只用于验证新盘可写，复制结束后会删除。

---

## 六、停止写入并备份 Docker 数据

### 1. 结束所有会写工作区的任务

先在 AI Harness 中确认当前 Agent 任务已经结束，不要在有 coder/reviewer 正在写文件时停 Docker。

### 2. 停止 Docker 和容器

```bash
sudo systemctl stop docker.socket docker.service containerd.service
sudo pgrep -a dockerd || true
sudo pgrep -a containerd || true
```

如果还有进程，等待它们退出后再复制。不要直接复制运行中的 Docker 数据目录。

### 3. 备份关键配置

```bash
sudo install -d -m 0700 /var/backups/docker-migration

sudo cp -a /etc/docker /var/backups/docker-migration/ 2>/dev/null || true
sudo cp -a /etc/containerd /var/backups/docker-migration/ 2>/dev/null || true
sudo cp -a /etc/systemd/system/docker.service.d \
  /var/backups/docker-migration/ 2>/dev/null || true

sudo cp -a /etc/fstab /var/backups/docker-migration/fstab.before
```

不要把这个备份目录上传到公开位置；配置中可能包含镜像仓库凭据或私有仓库地址。

### 4. 全量复制 Docker 数据

注意源路径末尾的 `/`，它表示复制目录中的内容，而不是额外套一层 `docker` 目录：

```bash
sudo rsync -aHAX --numeric-ids --info=progress2 \
  /var/lib/docker/ /mnt/docker-migrate/
```

完成后刷新磁盘：

```bash
sudo rm -f /mnt/docker-migrate/.migration-start
sync
```

### 5. 校验复制结果

先做 dry-run 校验。没有输出通常表示双方一致：

```bash
sudo rsync -aHAXnc --numeric-ids \
  /var/lib/docker/ /mnt/docker-migrate/
```

再核对目录大小：

```bash
sudo du -sh /var/lib/docker
sudo du -sh /mnt/docker-migrate
```

新文件系统会自动带一个 `lost+found`，所以 `mnt` 侧可能略大。不要只因为大小有几个字节或
块大小的差异就删除数据；以 `rsync` dry-run 无差异为准。

---

## 七、切换 `/var/lib/docker`

以下步骤必须保持 Docker 停止。

### 1. 卸载临时挂载

```bash
sudo umount /mnt/docker-migrate
findmnt /mnt/docker-migrate
```

### 2. 保留旧目录并挂入新盘

```bash
sudo mv /var/lib/docker /var/lib/docker.legacy
sudo mkdir -p /var/lib/docker
sudo mount /dev/NEW_DISK_PARTITION /var/lib/docker
findmnt /var/lib/docker
```

此时旧数据仍在 `/var/lib/docker.legacy`，不要删除。

### 3. 写入 `/etc/fstab`

获取 UUID：

```bash
sudo blkid /dev/NEW_DISK_PARTITION
```

编辑：

```bash
sudo nano /etc/fstab
```

增加一行，`UUID` 使用上一条命令的实际值：

```fstab
UUID=REPLACE_WITH_UUID  /var/lib/docker  ext4  defaults,noatime  0  2
```

校验挂载：

```bash
sudo mount -a
findmnt /var/lib/docker
df -hT /var/lib/docker
```

不要加 `nofail`。如果新盘挂载失败，Docker 不应悄悄回退到根分区创建一套新的空数据目录。

### 4. 让 Docker 启动依赖这个挂载点

```bash
sudo systemctl edit docker.service
```

在编辑器中写入：

```ini
[Unit]
RequiresMountsFor=/var/lib/docker
```

保存后：

```bash
sudo systemctl daemon-reload
```

---

## 八、启动与验证

### 1. 启动 Docker

```bash
sudo systemctl start containerd.service
sudo systemctl start docker.service
```

### 2. 验证 Docker 使用的是新盘

```bash
sudo docker info --format '{{.DockerRootDir}} {{.Driver}}'
findmnt /var/lib/docker
df -hT /var/lib/docker
```

预期 Docker Root Dir 仍是 `/var/lib/docker`，但底层已经是新盘。

### 3. 核对镜像、容器和卷

```bash
sudo docker images
sudo docker ps -a
sudo docker volume ls
sudo docker system df -v
```

至少确认：

- `opensandbox/code-interpreter:v1.1.0` 存在；
- 原有容器和命名卷仍在；
- 工作区卷 `ai-coding-ws-*` 没有丢失；
- Maven 缓存卷 `ai-coding-m2` 没有丢失。

### 4. 验证 OpenSandbox

```bash
curl -fsS http://127.0.0.1:8080/health
```

然后在 AI Harness 中重新打开原会话，确认工作区文件仍可读。必要时让 OpenSandbox
从原命名卷重建容器；销毁容器不等于销毁卷。

---

## 九、PostgreSQL 与 Redis 恢复

Docker 数据迁走后，先观察而不急着操作数据库。

### PostgreSQL

```bash
pg_isready -h 127.0.0.1 -p 5432
sudo journalctl -u postgresql -n 120 --no-pager
```

如果 recovery 完成，日志会进入 accepting connections。若长时间没有进展，再结合日志决定是否
重启 PostgreSQL；不要删除 `pg_wal`。

验证：

```bash
sudo -u postgres psql -d ai_coding_harness -c "
select current_database(), pg_is_in_recovery(), now();
"
```

### Redis

```bash
redis-cli ping
redis-cli INFO persistence
```

重点看：

- `rdb_last_bgsave_status:ok`
- 写命令不再返回 `MISCONF`

不要通过设置 `stop-writes-on-bgsave-error no` 掩盖磁盘问题。

### 建议的数据库备份

PostgreSQL 恢复后：

```bash
sudo install -d -m 0700 /var/backups/ai-coding-harness

backup="/var/backups/ai-coding-harness/ai_coding_harness_$(date +%Y%m%d-%H%M%S).dump"
sudo -u postgres pg_dump -Fc ai_coding_harness | sudo tee "$backup" >/dev/null
sudo chmod 600 "$backup"
sudo pg_restore --list "$backup" >/dev/null && echo "backup ok: $backup"
```

Redis 使用现有认证信息执行一次 `BGSAVE`，确认
`rdb_last_bgsave_status:ok` 后，再把 RDB/AOF 纳入备份策略。

---

## 十、确认 Docker 迁移无误后释放旧目录

> **关键：`/var/lib/docker.legacy` 不删除，根分区就没有真正释放空间。** Docker 虽然已经从
> 新盘启动，但 PostgreSQL 和 Redis 仍会因为原根分区 100% 而继续处于 recovery / MISCONF。
> 这一步不是可选的“以后再说”，而是数据库恢复前的必要步骤。

PostgreSQL 和 Redis 的恢复依赖根分区重新获得空间，所以应先完成 Docker 侧验证，再删除旧目录。
删除前至少确认：

1. Docker daemon 正常；
2. 镜像、容器、卷数量与迁移前一致；
3. OpenSandbox 健康；
4. 至少重新打开一个原会话并读取工作区；
5. `sudo rsync -aHAXnc --numeric-ids /var/lib/docker.legacy/ /var/lib/docker/`
   dry-run 没有输出；
6. `/var/lib/docker` 确实来自新盘，而不是根分区上的空目录。

先执行机器可读的防呆检查：

```bash
# 必须是 /dev/新盘对应分区，例如 /dev/sdb1
findmnt -no SOURCE,FSTYPE,TARGET /var/lib/docker

# Docker 必须仍在运行且 root dir 是 /var/lib/docker
sudo docker info --format '{{.DockerRootDir}} {{.Driver}}'

# 新盘必须有足够剩余空间；不要看错成根分区
df -hT /var/lib/docker
```

满足后再执行：

```bash
sudo rm -rf --one-file-system /var/lib/docker.legacy
sync
df -hT /
findmnt -no SOURCE,TARGET /var/lib/docker
```

`--one-file-system` 可以避免误进入旧目录下面残留的其他挂载点。删除后旧 Docker 数据无法从
本机直接恢复，只能依赖新盘副本或虚拟机快照。

删除后立即检查根分区是否已经出现可用空间。随后等待 PostgreSQL 完成 recovery；Redis 如果
没有自动恢复，再在空间充足的前提下执行 `BGSAVE` 并确认
`rdb_last_bgsave_status:ok`。数据库恢复后再执行本文的 PostgreSQL 逻辑备份。

---

## 十一、回滚方案

如果 Docker 启动失败，或镜像/卷不完整：

```bash
sudo systemctl stop docker.service docker.socket containerd.service

# 卸载新盘
sudo umount /var/lib/docker

# 给失败的新目录改个名字，保留现场
sudo mv /var/lib/docker /var/lib/docker.new-failed

# 恢复旧目录
sudo mv /var/lib/docker.legacy /var/lib/docker

sudo systemctl start containerd.service
sudo systemctl start docker.service
```

同时修正或注释 `/etc/fstab` 中的新挂载行，否则下一次启动还会尝试挂载新盘。

---

## 十二、最终检查清单

- [ ] 新盘是单独虚拟磁盘，不是误操作系统盘
- [ ] 新盘容量 80–100 GiB
- [ ] Docker 停止后才开始复制
- [ ] `rsync` 复制成功且 dry-run 无差异
- [ ] `/var/lib/docker` 已挂载新盘
- [ ] `/etc/fstab` 使用 UUID
- [ ] Docker 服务设置了 `RequiresMountsFor=/var/lib/docker`
- [ ] 镜像、容器、命名卷完整
- [ ] OpenSandbox 健康
- [ ] 原会话工作区可读
- [ ] Docker 验证通过并释放了 `docker.legacy`
- [ ] PostgreSQL 已离开 recovery
- [ ] Redis RDB 保存恢复为 `ok`
- [ ] PostgreSQL 逻辑备份已生成
- [ ] 最终重启验证通过

---

## 十三、附录：`/var/lib/containerd` 仍占用大量空间

Ubuntu 24.04 同时运行 Docker 和 containerd。部分 Docker 配置会把镜像内容或 snapshot
放到 `/var/lib/containerd`，因此只迁移 `/var/lib/docker` 不代表所有容器运行数据都已离开
根分区。

### Docker 29 + containerd snapshotter

Docker 29 可能显示：

```text
Docker Root Dir: /var/lib/docker
Storage Driver: overlayfs
  driver-type: io.containerd.snapshotter.v1
```

这时 `/var/lib/docker` 只保存 Docker 自身的配置、卷和部分元数据；镜像 content 与
overlayfs snapshots 位于：

```text
/var/lib/containerd/io.containerd.content.v1.content
/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs
```

这类数据是镜像的实际内容，不能清理。必须把 `/var/lib/containerd` 也迁移到独立磁盘。

先做只读定位：

```bash
df -hT / /var /var/lib/docker /var/lib/containerd
sudo du -xhd1 /var | sort -h
sudo du -xhd1 /var/lib | sort -h
sudo du -xhd2 /var/lib/containerd | sort -h

sudo findmnt -T /var/lib/containerd
sudo find /var/lib/containerd -xdev -type f -printf '%s\t%p\n' \
  | sort -nr | head -30

sudo journalctl --disk-usage
sudo du -sh /var/log /var/cache/apt /var/lib/postgresql /var/lib/redis

sudo docker info
sudo containerd config dump
sudo ctr namespaces list
sudo ctr -n moby images list
sudo ctr -n k8s.io images list
```

判断方式：

- `docker info` 的存储驱动是 `overlay2`，且 `ctr -n moby images list` 基本为空：
  `/var/lib/containerd` 可能是历史或未引用内容，先确认时间、引用和日志，再走 containerd
  自己的 GC/prune，不能手工删 blobs。
- `docker info` 使用 containerd snapshotter，或 `moby` 命名空间里能看到
  `opensandbox/code-interpreter`：这是活跃容器数据，必须备份并迁移，不能清理。
- `k8s.io` 命名空间有镜像：说明还有 Kubernetes 工作负载使用同一 containerd，不能按
  Docker 单机数据随意处置。

如果 containerd 数据必须保留，应像 Docker 一样先停 `docker.service` 和
`containerd.service`，把 `/var/lib/containerd` 复制到新盘的独立子目录，再通过独立挂载或
bind mount 放回 `/var/lib/containerd`。不要把 containerd 的 `content` 或 snapshots 当作
普通缓存直接删除。
