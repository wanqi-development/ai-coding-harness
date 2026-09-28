# Google Go 风格决策

> 译自 Google 的 [Go Style Decisions](https://google.github.io/styleguide/go/decisions)，
> 原文以 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 许可发布。
> 本文件是其简体中文译本，供编码与审查时查阅：标题的层级与顺序照原文，
> 代码示例、标识符与注释一律保留英文原文。
>
> 原文对这四份文档的定位：本页是**规范性**的，但**不是权威性**的，从属于[指南](go-style.md)；
> 两者冲突时以指南为准（见文首「关于」一节）。


**Note:** 这是一系列文档中的一篇，这些文档阐述了 Google 的 [Go Style](go-style.md)。本文档是**[规范性](go-style.md#normative)但非[权威](go-style.md#canonical)**的，从属于[核心风格指南](go-style.md)。更多信息请见[概览](go-style.md#about)。

<a id="about"></a>

## 关于

本文档包含一系列风格决策，旨在为 Go 可读性导师给出的建议提供统一、标准的指导、解释与示例。

本文档**并不穷尽**，会随时间不断扩充。若[核心风格指南](go-style.md)与本文给出的建议相抵触，**以风格指南为准**，本文档应相应更新。

关于完整的 Go Style 文档集，请见[概览](go-style.md#about)。

以下章节已从风格决策移至指南的其他部分：

*   **MixedCaps**：见 [guide#mixed-caps](go-style.md#mixed-caps)
    <a id="mixed-caps"></a>

*   **Formatting**：见 [guide#formatting](go-style.md#formatting)
    <a id="formatting"></a>

*   **Line Length**：见 [guide#line-length](go-style.md#line-length)
    <a id="line-length"></a>

<a id="naming"></a>

## 命名

关于命名的总体指导，请见[核心风格指南](go-style.md#naming)中的命名章节。以下各节对命名中的具体领域作进一步说明。

<a id="underscores"></a>

### 下划线

Go 中的名称一般不应包含下划线。此原则有三种例外：

1.  仅由生成代码导入的包名可以包含下划线。关于如何选择多词包名的更多细节，请见[包名](#package-names)。
1.  `*_test.go` 文件中的 Test、Benchmark 和 Example 函数名可以包含下划线。
1.  与操作系统或 cgo 交互的底层库可以复用标识符，就像 [`syscall`] 中所做的那样。预计在大多数代码库中这种情况非常罕见。

**注意：** 源代码的文件名不是 Go 标识符，不必遵循这些约定。它们可以包含下划线。

[`syscall`]: https://pkg.go.dev/syscall#pkg-constants

<a id="package-names"></a>

### 包名

<a id="TOC-PackageNames"></a>

在 Go 中，包名必须简洁，且只能使用小写字母和数字（例如 [`k8s`]、[`oauth2`]）。多词包名应保持不拆分、全部小写（例如 [`tabwriter`]，而非 `tabWriter`、`TabWriter` 或 `tab_writer`）。

避免选用可能被常用局部变量名[shadowed]的包名。例如，`usercount` 是比 `count` 更好的包名，因为 `count` 是常用的变量名。

Go 包名不应包含下划线。如果你需要导入的包名中确实带下划线（通常来自生成代码或第三方代码），必须在导入时把它重命名为适合在 Go 代码中使用的名称。

例外情况是，仅由生成代码导入的包名可以包含下划线。具体示例包括：

*   对仅测试包导出 API 的单元测试使用 `_test` 后缀（`testing` 包称之为「[黑盒测试](https://pkg.go.dev/testing)」）。例如，包 `linkedlist` 必须在一个名为 `linkedlist_test`（而非 `linked_list_test`）的包中定义其黑盒单元测试

*   对指定功能测试或集成测试的包使用下划线和 `_test` 后缀。例如，一个链表服务的集成测试可以命名为 `linked_list_service_test`

*   对[包级文档示例](https://go.dev/blog/examples)使用 `_test` 后缀

[`tabwriter`]: https://pkg.go.dev/text/tabwriter
[`k8s`]: https://pkg.go.dev/k8s.io/client-go/kubernetes
[`oauth2`]: https://pkg.go.dev/golang.org/x/oauth2
[shadowed]: go-best-practices.md#shadowing

避免使用 `util`、`utility`、`common`、`helper`、`model`、`testhelper` 之类缺乏信息量的包名，它们会诱使包的使用者在[导入时重命名](#import-renaming)。见：

*   [关于所谓「工具包」的指导](go-best-practices.md#util-packages)
*   [Go Tip #97：名字里有什么](go-style.md#gotip)
*   [Go Tip #108：好包名的力量](go-style.md#gotip)

当导入的包被重命名时（例如 `import foopb "path/to/foo_go_proto"`），该包的本地名必须符合上述规则，因为本地名决定了文件中如何引用包内的符号。如果某个导入在多个文件中被重命名，尤其是在同一个包或相邻包中，为保持一致，应尽可能使用相同的本地名。

<!--#include file="/go/g3doc/style/includes/special-name-exception.md"-->

另见：[关于包名的 Go 博客文章](https://go.dev/blog/package-names)。

<a id="receiver-names"></a>

### 接收者名称

<a id="TOC-ReceiverNames"></a>

[Receiver] 变量名必须：

*   简短（通常为一两个字母）
*   是类型本身的缩写
*   对该类型的每个接收者一致地使用
*   不能是下划线；如果未使用则省略名称

长名称                      | 更好的名称
--------------------------- | -------------------------
`func (tray Tray)`          | `func (t Tray)`
`func (info *ResearchInfo)` | `func (ri *ResearchInfo)`
`func (this *ReportWriter)` | `func (w *ReportWriter)`
`func (self *Scanner)`      | `func (s *Scanner)`

[Receiver]: https://golang.org/ref/spec#Method_declarations

<a id="constant-names"></a>

### 常量名

常量名必须像 Go 中所有其他名称一样使用 [MixedCaps]。（[Exported] 常量以大写字母开头，未导出常量以小写字母开头。）即使这违反其他语言的约定也应当如此。常量名不应是其值的派生，而应说明该值代表什么。

```go
// Good:
const MaxPacketSize = 512

const (
    ExecuteBit = 1 << iota
    WriteBit
    ReadBit
)
```

[MixedCaps]: go-style.md#mixed-caps
[Exported]: https://tour.golang.org/basics/3

不要使用非 MixedCaps 的常量名或带 `K` 前缀的常量。

```go
// Bad:
const MAX_PACKET_SIZE = 512
const kMaxBufferSize = 1024
const KMaxUsersPergroup = 500
```

应根据常量的作用而非其值来命名常量。如果一个常量除了它的值之外没有别的作用，那就不必把它定义为常量。

```go
// Bad:
const Twelve = 12

const (
    UserNameColumn = "username"
    GroupColumn    = "group"
)
```

<!--#include file="/go/g3doc/style/includes/special-name-exception.md"-->

<a id="initialisms"></a>

### 首字母缩写词

<a id="TOC-Initialisms"></a>

名称中属于首字母缩写词或首字母缩略语的单词（例如 `URL` 和 `NATO`）应当大小写一致。`URL` 应写作 `URL` 或 `url`（如 `urlPony` 或 `URLPony`），绝不能写作 `Url`。作为一般规则，标识符（例如 `ID` 和 `DB`）的大小写也应与其在英文行文中的用法相似。

*   在包含多个首字母缩写词的名称中（例如 `XMLAPI`，因为它包含 `XML` 和 `API`），同一个缩写词内的每个字母大小写应一致，但名称中各个缩写词之间不必大小写相同。
*   在包含带小写字母的缩写词的名称中（例如 `DDoS`、`iOS`、`gRPC`），该缩写词应按其在标准行文中的样子出现，除非你出于[exportedness]的需要而改动首字母。在这些情况下，整个缩写词应大小写一致（例如 `ddos`、`IOS`、`GRPC`）。

[exportedness]: https://golang.org/ref/spec#Exported_identifiers

<!-- 保持此表格窄。如果它必须变宽，请改用列表。 -->

英文用法 | 作用域     | 正确     | 错误
------------- | ---------- | -------- | --------------------------------------
XML API       | 导出       | `XMLAPI` | `XmlApi`, `XMLApi`, `XMLAPI`, `XMLapi`
XML API       | 未导出     | `xmlAPI` | `xmlapi`, `xmlApi`
iOS           | 导出       | `IOS`    | `Ios`, `IoS`
iOS           | 未导出     | `iOS`    | `ios`
gRPC          | 导出       | `GRPC`   | `Grpc`
gRPC          | 未导出     | `gRPC`   | `grpc`
DDoS          | 导出       | `DDoS`   | `DDOS`, `Ddos`
DDoS          | 未导出     | `ddos`   | `dDoS`, `dDOS`
ID            | 导出       | `ID`     | `Id`
ID            | 未导出     | `id`     | `iD`
DB            | 导出       | `DB`     | `Db`
DB            | 未导出     | `db`     | `dB`
Txn           | 导出       | `Txn`    | `TXN`

<!--#include file="/go/g3doc/style/includes/special-name-exception.md"-->

<a id="getters"></a>

### Getter

<a id="TOC-Getters"></a>

函数和方法名不应使用 `Get` 或 `get` 前缀，除非底层概念本身就用了「get」这个词（例如 HTTP GET）。更倾向于直接以名词开头，例如用 `Counts` 而非 `GetCounts`。

如果函数涉及执行复杂计算或远程调用，可以用 `Compute` 或 `Fetch` 之类的其他词代替 `Get`，以便向读者表明该函数调用可能耗时，并且可能阻塞或失败。

<!--#include file="/go/g3doc/style/includes/special-name-exception.md"-->

<a id="variable-names"></a>

### 变量名

<a id="TOC-VariableNames"></a>

一般的经验法则是，名称的长度应与其作用域的大小成正比，与该作用域内使用它的次数成反比。在文件作用域创建的变量可能需要多个单词，而作用域限于单个内部块的变量可以只是一个单词，甚至一两个字符，以保持代码清晰并避免多余信息。

下面是一个粗略的基准。这些数值指南并非严格规则。请根据上下文、[clarity] 和 [concision] 来作判断。

*   小作用域是执行一两个小操作的作用域，比如 1-7 行。
*   中等作用域是几个小操作或一个大操作，比如 8-15 行。
*   大作用域是一个或几个大操作，比如 15-25 行。
*   超大作用域是任何超过一页的（比如超过 25 行）作用域。

[clarity]: go-style.md#clarity
[concision]: go-style.md#concision

在小作用域内可能完全清晰的名称（例如用 `c` 表示计数器），在更大的作用域中可能就不够用，需要加以澄清，以便在代码更靠后的位置提醒读者它的用途。如果作用域中有许多变量，或者有表示相似值或概念的变量，那么变量名可能需要比该作用域所暗示的更长。

概念的具体程度也有助于保持变量名简洁。例如，假设只使用一个数据库，那么像 `db` 这样通常留给极小作用域的短变量名，即使作用域非常大也可能仍然完全清晰。在这种情况下，根据作用域的大小，单个词 `database` 大概也可以接受，但并非必需，因为 `db` 是该词非常常见的缩写，几乎没有其他解释。

局部变量的名称应反映它包含什么以及它在当前上下文中如何使用，而不是该值源自哪里。例如，最好的局部变量名往往与结构体或 protocol buffer 字段名不同。

一般来说：

*   像 `count` 或 `options` 这样的单个词名称是一个很好的起点。
*   可以添加额外的词来区分相似的名称，例如 `userCount` 和 `projectCount`。
*   不要仅仅为了少打字而删减字母。例如，`Sandbox` 优于 `Sbx`，尤其是对导出名称而言。
*   从大多数变量名中省略[types and type-like words]。
    *   对于数字，`userCount` 是比 `numUsers` 或 `usersInt` 更好的名称。
    *   对于切片，`users` 是比 `userSlice` 更好的名称。
    *   如果作用域中有同一个值的两个版本，可以接受包含类类型的限定词，例如你可能把输入存储在 `ageString` 中，而用 `age` 表示解析后的值。
*   省略从[surrounding context]中就能看清楚的词。例如，在 `UserCount` 方法的实现中，名为 `userCount` 的局部变量很可能是冗余的；`count`、`users` 甚至 `c` 同样易读。

[types and type-like words]: #repetitive-with-type
[surrounding context]: #repetitive-in-context

<a id="v"></a>

#### 单字母变量名

单字母变量名可以是减少[重复](#repetition)的有用工具，但也可能使代码变得毫无必要地晦涩。只应在完整单词显而易见、且用它替代单字母变量会造成重复的情况下才使用。

一般来说：

*   对于[method receiver variable]，更倾向于用一两个字母的名称。
*   为常见类型使用熟悉的变量名往往有帮助：
    *   用 `r` 表示 `io.Reader` 或 `*http.Request`
    *   用 `w` 表示 `io.Writer` 或 `http.ResponseWriter`
*   单字母标识符可以接受用作整数循环变量，尤其是索引（例如 `i`）和坐标（例如 `x` 和 `y`）。
*   当作用域很短时，缩写可以接受用作循环标识符，例如 `for _, n := range nodes { ... }`。

[method receiver variable]: #receiver-names

<a id="repetition"></a>

### 重复冗余

<!--
给未来编辑者的说明：

当名称重复时，不要用「stutter」这个词来指代。
-->

一段 Go 源代码应避免不必要的重复。一个常见来源是重复的名称，它们往往包含多余的词，或重复其上下文或类型。如果相同或相似的代码段在相近位置多次出现，代码本身也可能不必要地重复。

重复的命名有多种形式，包括：

<a id="repetitive-with-package"></a>

#### 包名与导出符号名

在给导出符号命名时，包名在你的包之外总是可见的，因此两者之间的冗余信息应减少或消除。如果一个包只导出一个类型，且该类型以包本身命名，那么构造函数（若需要）的权威名称是 `New`。

> **示例：** 重复的名称 -> 更好的名称
>
> *   `widget.NewWidget` -> `widget.New`
> *   `widget.NewWidgetWithName` -> `widget.NewWithName`
> *   `db.LoadFromDatabase` -> `db.Load`
> *   `goatteleportutil.CountGoatsTeleported` -> `gtutil.CountGoatsTeleported`
>     或 `goatteleport.Count`
> *   `myteampb.MyTeamMethodRequest` -> `mtpb.MyTeamMethodRequest` 或
>     `myteampb.MethodRequest`

<a id="repetitive-with-type"></a>

#### 变量名与类型

编译器总是知道变量的类型，而且在大多数情况下，读者通过变量的用法也能清楚它是什么类型。只有当同一个值在同一作用域中出现两次时，才有必要澄清变量的类型。

重复的名称                    | 更好的名称
----------------------------- | ----------------------
`var numUsers int`            | `var users int`
`var nameString string`       | `var name string`
`var primaryProject *Project` | `var primary *Project`

如果该值以多种形式出现，可以用 `raw` 和 `parsed` 之类额外的词来澄清，也可以用底层表示来澄清：

```go
// Good:
limitRaw := r.FormValue("limit")
limit, err := strconv.Atoi(limitRaw)
```

```go
// Good:
limitStr := r.FormValue("limit")
limit, err := strconv.Atoi(limitStr)
```

<a id="repetitive-in-context"></a>

#### 外部上下文与局部名称

包含周围上下文信息的名称往往会带来无益的额外噪音。包名、方法名、类型名、函数名、导入路径，甚至文件名，都能提供自动限定其中所有名称的上下文。

```go
// Bad:
// In package "ads/targeting/revenue/reporting"
type AdsTargetingRevenueReport struct{}

func (p *Project) ProjectName() string
```

```go
// Good:
// In package "ads/targeting/revenue/reporting"
type Report struct{}

func (p *Project) Name() string
```

```go
// Bad:
// In package "sqldb"
type DBConnection struct{}
```

```go
// Good:
// In package "sqldb"
type Connection struct{}
```

```go
// Bad:
// In package "ads/targeting"
func Process(in *pb.FooProto) *Report {
    adsTargetingID := in.GetAdsTargetingID()
}
```

```go
// Good:
// In package "ads/targeting"
func Process(in *pb.FooProto) *Report {
    id := in.GetAdsTargetingID()
}
```

重复通常应放在符号使用者的上下文中来评估，而不是孤立地评估。例如，下面的代码有很多名称，它们在有些情况下可能没问题，但在上下文中是冗余的：

```go
// Bad:
func (db *DB) UserCount() (userCount int, err error) {
    var userCountInt64 int64
    if dbLoadError := db.LoadFromDatabase("count(distinct users)", &userCountInt64); dbLoadError != nil {
        return 0, fmt.Errorf("failed to load user count: %s", dbLoadError)
    }
    userCount = int(userCountInt64)
    return userCount, nil
}
```

反之，名称中从上下文或用法就能看出的信息往往可以省略：

```go
// Good:
func (db *DB) UserCount() (int, error) {
    var count int64
    if err := db.Load("count(distinct users)", &count); err != nil {
        return 0, fmt.Errorf("failed to load user count: %s", err)
    }
    return int(count), nil
}
```

<a id="commentary"></a>

## 注释

围绕注释的约定（包括注释什么、用什么风格、如何提供可运行的示例等）旨在支撑阅读公共 API 文档的体验。更多信息请见 [Effective Go](http://golang.org/doc/effective_go.html#commentary)。

最佳实践文档中关于[documentation conventions]的一节对此有进一步讨论。

**最佳实践：** 在开发和代码审查期间使用[doc preview]，看看文档和可运行示例是否有用，以及是否以你期望的方式呈现。

**提示：** Godoc 使用的特殊格式很少；列表和代码片段通常应缩进以避免换行。除缩进之外，一般应避免装饰。

[doc preview]: go-best-practices.md#documentation-preview
[documentation conventions]:  go-best-practices.md#documentation-conventions

<a id="comment-line-length"></a>

### 注释行长度

Go 中注释没有固定的[line length]。

[line length]: go-style.md#line-length

长的注释行应当折行，以确保源代码在不自动折行注释行的工具中可读。如果不确定在哪里折行，80 或 100 列是常见选择。然而这并不是硬性界限；有些情况下断开很长的字面文本是有害的。对折行发生在哪一具体列宽并没有要求。力求在一个文件内保持[一致](go-style.md#consistency)。

关于注释的更多内容，请见 [The Go Blog 上关于文档的这篇文章]。

[post from The Go Blog on documentation]: https://blog.golang.org/godoc-documenting-go-code

```text
# Good:
// This is a comment paragraph.
// The length of individual lines doesn't matter in Godoc;
// but the choice of wrapping makes it easy to read on narrow screens.
//
// Don't worry too much about the long URL:
// https://supercalifragilisticexpialidocious.example.com:8080/Animalia/Chordata/Mammalia/Rodentia/Geomyoidea/Geomyidae/
//
// Similarly, if you have other information that is made awkward
// by too many line breaks, use your judgment and include a long line
// if it helps rather than hinders.
```

避免把大量文本塞进单行的注释，这会带来糟糕的阅读体验。

```text
# Bad:
// This is a comment paragraph. While some code editors and viewers will wrap the paragraph for the reader, others will display a very long line that will overflow most windows and require users to scroll horizontally. In addition, even on a screen capable of displaying the entire line, it is easier to read a narrower paragraph than very wide one.
//
// Don't worry too much about the long URL:
// https://supercalifragilisticexpialidocious.example.com:8080/Animalia/Chordata/Mammalia/Rodentia/Geomyoidea/Geomyidae/
```

<a id="doc-comments"></a>

### 文档注释

<a id="TOC-DocComments"></a>

所有顶层的导出名称都必须有文档注释，行为或含义不明显的未导出类型或函数声明也应如此。这些注释应当是[full sentences]，并以所描述对象的名称开头。可以在名称前加冠词（「a」「an」「the」）使其读起来更自然。

```go
// Good:
// A Request represents a request to run a command.
type Request struct { ...

// Encode writes the JSON encoding of req to w.
func Encode(w io.Writer, req *Request) { ...
```

文档注释会出现在 [Godoc](https://pkg.go.dev/) 中并由 IDE 呈现，因此应面向任何使用该包的人来写。

[full sentences]: #comment-sentences

文档注释适用于紧随其后的符号；如果出现在结构体中，则适用于该组字段。

```go
// Good:
// Options configure the group management service.
type Options struct {
    // General setup:
    Name  string
    Group *FooGroup

    // Dependencies:
    DB *sql.DB

    // Customization:
    LargeGroupThreshold int // optional; default: 10
    MinimumMembers      int // optional; default: 2
}
```

**最佳实践：** 如果你为未导出代码写了文档注释，请遵循与导出代码相同的惯例（即以未导出的名称开头写注释）。这样以后要导出它就很容易，只需在注释和代码中把未导出的名称替换为新导出的名称。

<a id="comment-sentences"></a>

### 注释句子

<a id="TOC-CommentSentences"></a>

完整的句子形式的注释应按标准英文句子那样大写并加标点。（作为例外，如果意思清楚，句子以未大写的标识符名开头也可以。这类情况最好只在段首出现。）

句子片段形式的注释没有这类标点或大写的要求。

[Documentation comments] 应始终是完整的句子，因此应始终大写并加标点。简单的行尾注释（尤其是结构体字段的）可以是简单的短语，假定字段名就是主语。

```go
// Good:
// A Server handles serving quotes from the collected works of Shakespeare.
type Server struct {
    // BaseDir points to the base directory under which Shakespeare's works are stored.
    //
    // The directory structure is expected to be the following:
    //   {BaseDir}/manifest.json
    //   {BaseDir}/{name}/{name}-part{number}.txt
    BaseDir string

    WelcomeMessage  string // displayed when user logs in
    ProtocolVersion string // checked against incoming requests
    PageLength      int    // lines per page when printing (optional; default: 20)
}
```

[Documentation comments]: #doc-comments

<a id="examples"></a>

### 示例

<a id="TOC-Examples"></a>

包应清楚地记录其预期用法。尽量提供一个[runnable example]；示例会出现在 Godoc 中。可运行的示例属于测试文件，而不是生产源文件。请见这个示例（[Godoc]、[source]）。

[runnable example]: http://blog.golang.org/examples
[Godoc]: https://pkg.go.dev/time#example-Duration
[source]: https://cs.opensource.google/go/go/+/HEAD:src/time/example_test.go

如果提供可运行的示例不可行，可以在代码注释中提供示例代码。与其他注释中的代码和命令行片段一样，它应遵循标准的格式约定。

<a id="named-result-parameters"></a>

### 命名的结果参数

<a id="TOC-NamedResultParameters"></a>

命名参数时，要考虑函数签名在 Godoc 中如何呈现。函数自身的名字与结果参数的类型往往已经足够清晰。

```go
// Good:
func (n *Node) Parent1() *Node
func (n *Node) Parent2() (*Node, error)
```

如果函数返回两个或更多同类型的参数，加上名字会有所帮助。

```go
// Good:
func (n *Node) Children() (left, right *Node, err error)
```

如果调用方必须对特定的结果参数采取行动，给它们命名有助于提示该行动是什么：

```go
// Good:
// WithTimeout returns a context that will be canceled no later than d duration
// from now.
//
// The caller must arrange for the returned cancel function to be called when
// the context is no longer needed to prevent a resource leak.
func WithTimeout(parent Context, d time.Duration) (ctx Context, cancel func())
```

在上面的代码中，取消是调用方必须采取的一项特定行动。然而，如果结果参数只写成 `(Context, func())`，就难以看出「cancel function」指的是什么。

当命名会产生[不必要的重复](#repetitive-with-type)时，不要使用命名的结果参数。

```go
// Bad:
func (n *Node) Parent1() (node *Node)
func (n *Node) Parent2() (node *Node, err error)
```

不要为了省去在函数内部声明变量而给结果参数命名。这种做法以略微精简实现为代价，换来了不必要的 API 冗长。

[裸返回]仅在小函数中可以接受。一旦函数达到中等规模，就要明确写出返回的值。同样，不要仅仅因为命名结果参数能让你使用裸返回就给它命名。[清晰](go-style.md#clarity)永远比在函数里省下几行更重要。

如果某个结果参数的值必须在延迟闭包中被修改，那么给它命名总是可以接受的。

> **提示：** 在函数签名中，类型往往比名字更清晰。[GoTip #38: Functions as Named Types] 演示了这一点。
>
> 在上面 [`WithTimeout`] 的真实代码中，结果参数列表用的是 [`CancelFunc`] 而不是裸的 `func()`，文档化几乎不费力气。

[Naked returns]: https://tour.golang.org/basics/7
[GoTip #38: Functions as Named Types]: go-style.md#gotip
[`WithTimeout`]: https://pkg.go.dev/context#WithTimeout
[`CancelFunc`]: https://pkg.go.dev/context#CancelFunc

<a id="package-comments"></a>

### 包注释

<a id="TOC-PackageComments"></a>

包注释必须紧挨在包子句上方出现，注释与包名之间不能有空行。例如：

```go
// Good:
// Package math provides basic constants and mathematical functions.
//
// This package does not guarantee bit-identical results across architectures.
package math
```

每个包只能有一个包注释。如果一个包由多个文件组成，应当恰好有一个文件带有包注释。

`main` 包的注释形式略有不同，其中由 BUILD 文件中 `go_binary` 规则的名字代替包名。

```go
// Good:
// The seed_generator command is a utility that generates a Finch seed file
// from a set of JSON study configs.
package main
```

只要二进制的名字与 BUILD 文件中写的完全一致，其他注释风格也可以。当二进制名是第一个词时，必须首字母大写，即便它与命令行调用时的拼写并不严格一致。

```go
// Good:
// Binary seed_generator ...
// Command seed_generator ...
// Program seed_generator ...
// The seed_generator command ...
// The seed_generator program ...
// Seed_generator ...
```

提示：

*   示例命令行调用与 API 用法可以成为有用的文档。为了 Godoc 的格式，请缩进包含代码的注释行。

*   如果没有明显的主文件，或者包注释特别长，可以把文档注释放在一个名为 `doc.go` 的文件里，其中只含该注释与包子句。

*   可以用多行注释代替多个单行注释。当文档中包含便于从源文件复制粘贴的片段时（例如（面向二进制的）示例命令行与模板示例），这样做尤其有用。

    ```go
    // Good:
    /*
    The seed_generator command is a utility that generates a Finch seed file
    from a set of JSON study configs.

        seed_generator *.json | base64 > finch-seed.base64
    */
    package template
    ```

*   面向维护者、且适用于整个文件的注释通常放在导入声明之后。它们不会出现在 Godoc 中，也不受上述包注释规则的约束。

<a id="imports"></a>

## 导入

<a id="TOC-Imports"></a>

<a id="import-renaming"></a>

### 导入重命名

包的导入通常不应重命名，但有些情况下必须重命名，或者重命名能提升可读性。

导入包的本地名字必须遵循[关于包命名的指引](#package-names)，包括禁止使用下划线与大写字母。要尽量保持[一致](go-style.md#consistency)，对同一个导入包始终使用同一个本地名字。

为避免与其他导入发生名字冲突，被导入的包*必须*重命名。（由此可推知，[好的包名](#package-names)应当不需要重命名。）发生名字冲突时，优先重命名最局部的或项目特有的那个导入。

生成的 protocol buffer 包*必须*重命名，以去掉名字中的下划线，且其本地名字必须带有 `pb` 后缀。更多信息参见 [proto 与 stub 最佳实践](go-best-practices.md#import-protos)。

```go
// Good:
import (
    foosvcpb "path/to/package/foo_service_go_proto"
)
```

最后，一个被导入的、非自动生成的包如果名字缺乏信息量（例如 `util` 或 `v1`）*可以*重命名。请谨慎为之：如果使用该包处的周围代码已传达了足够的上下文，就不要重命名。条件允许时，优先重构该包本身，给它一个更合适的名字。

```go
// Good:
import (
    core "github.com/kubernetes/api/core/v1"
    meta "github.com/kubernetes/apimachinery/pkg/apis/meta/v1beta1"
)
```

如果你需要导入的包，其名字与你想要使用的某个常见局部变量名冲突（例如 `url`、`ssh`），并且你希望重命名该包，首选的做法是加上 `pkg` 后缀（例如 `urlpkg`）。注意，可以用局部变量遮蔽一个包；只有当该变量在作用域内时仍需要使用这个包，重命名才有必要。

<a id="import-grouping"></a>

### 导入分组

导入应当按顺序组织成以下几组：

1.  标准库包

1.  其他（项目内与 vendored）包

1.  Protocol Buffer 导入（例如 `fpb "path/to/foo_go_proto"`）

1.  为[副作用](https://go.dev/doc/effective_go#blank_import)而进行的导入（例如 `_ "path/to/package"`）

```go
// Good:
package main

import (
    "fmt"
    "hash/adler32"
    "os"

    "github.com/dsnet/compress/flate"
    "golang.org/x/text/encoding"
    "google.golang.org/protobuf/proto"

    foopb "myproj/foo/proto/proto"

    _ "myproj/rpc/protocols/dial"
    _ "myproj/security/auth/authhooks"
)
```

<a id="import-blank"></a>

### 导入 "blank"（`import _`）

<a id="TOC-ImportBlank"></a>

只为副作用而导入的包（使用 `import _ "package"` 语法）只能在 main 包中导入，或者在需要它们的测试中导入。

这类包的一些例子包括：

*   [time/tzdata](https://pkg.go.dev/time/tzdata)

*   [image/jpeg](https://pkg.go.dev/image/jpeg)，用于图像处理代码

在库包中避免使用空白导入，即便该库间接依赖它们。把副作用导入限制在 main 包中，有助于控制依赖，并使编写依赖不同导入的测试成为可能，而不会产生冲突或浪费构建成本。

以下是该规则仅有的例外：

*   可以使用空白导入绕过 [nogo 静态检查器] 对禁用导入的检查。

*   在使用 `//go:embed` 编译器指令的源文件中，可以空白导入 [embed](https://pkg.go.dev/embed) 包。

**提示：** 如果你创建的库包在生产中间接依赖某个副作用导入，请记录预期的用法。

[nogo static checker]: https://github.com/bazelbuild/rules_go/blob/master/go/nogo.rst

<a id="import-dot"></a>

### 导入 "dot"（`import .`）

<a id="TOC-ImportDot"></a>

`import .` 形式是一个语言特性，它允许把另一个包导出的标识符不加限定地带入当前包。更多内容参见[语言规范](https://go.dev/ref/spec#Import_declarations)。

在 Google 代码库中**不要**使用这一特性；它让人更难看出功能来自哪里。

```go
// Bad:
package foo_test

import (
    "bar/testutil" // also imports "foo"
    . "foo"
)

var myThing = Bar() // Bar defined in package foo; no qualification needed.
```

```go
// Good:
package foo_test

import (
    "bar/testutil" // also imports "foo"
    "foo"
)

var myThing = foo.Bar()
```

<a id="errors"></a>

## 错误

<a id="returning-errors"></a>

### 返回错误

<a id="TOC-ReturningErrors"></a>

用 `error` 来表示函数可能失败。按照约定，`error` 是最后一个结果参数。

```go
// Good:
func Good() error { /* ... */ }
```

返回 `nil` 错误是表示一个原本可能失败的操作成功的惯用做法。如果函数返回错误，调用方必须把所有非错误的返回值视为未指明，除非另有明确文档说明。通常，非错误的返回值是它们的零值，但不能想当然地这么认为。

```go
// Good:
func GoodLookup() (*Result, error) {
    // ...
    if err != nil {
        return nil, err
    }
    return res, nil
}
```

返回错误的导出函数应当使用 `error` 类型来返回。具体错误类型容易出微妙的 bug：一个具体的 `nil` 指针可能被包进接口，从而变成一个非 nil 的值（参见[关于该话题的 Go FAQ 条目][nil error]）。

```go
// Bad:
func Bad() *os.PathError { /*...*/ }
```

**提示：** 接受 [`context.Context`] 参数的函数通常应当返回 `error`，以便调用方判断函数运行期间上下文是否被取消。

[nil error]: https://golang.org/doc/faq#nil_error

<a id="error-strings"></a>

### 错误字符串

<a id="TOC-ErrorStrings"></a>

错误字符串不应首字母大写（除非以导出名、专有名词或首字母缩略语开头），也不应以标点结尾。这是因为错误字符串在打印给用户之前通常先出现在其他上下文中。

```go
// Bad:
err := fmt.Errorf("Something bad happened.")
```

```go
// Good:
err := fmt.Errorf("something bad happened")
```

另一方面，完整展示消息（日志、测试失败、API 响应或其他 UI）的风格则视情况而定，但通常应当首字母大写。

```go
// Good:
log.Infof("Operation aborted: %v", err)
log.Errorf("Operation aborted: %v", err)
t.Errorf("Op(%q) failed unexpectedly; err=%v", args, err)
```

<a id="handle-errors"></a>

### 处理错误

<a id="TOC-HandleErrors"></a>

遇到错误的代码应当有意识地选择如何处理它。通常不宜用 `_` 变量丢弃错误。如果函数返回错误，请做以下之一：

*   立即处理并解决该错误。
*   把错误返回给调用方。
*   在特殊情况下，调用 [`log.Fatal`]，或者（如果绝对必要）`panic`。

**注意：** `log.Fatalf` 不是标准库的 log。参见 [#logging]。

在少数适宜忽略或丢弃错误的情形下（例如调用 [`(*bytes.Buffer).Write`]，其文档说明它永不失败），应当附带一条注释解释为什么这样做是安全的。

```go
// Good:
var b *bytes.Buffer

n, _ := b.Write(p) // never returns a non-nil error
```

关于错误处理的更多讨论与示例，参见 [Effective Go](http://golang.org/doc/effective_go.html#errors) 与[最佳实践](go-best-practices.md#error-handling)。

[`(*bytes.Buffer).Write`]: https://pkg.go.dev/bytes#Buffer.Write

<a id="in-band-errors"></a>

### 带内错误

<a id="TOC-In-Band-Errors"></a>

在 C 及类似语言中，函数常返回 -1、null 或空字符串之类的值来表示错误或缺失的结果。这被称为带内错误处理。

```go
// Bad:
// Lookup returns the value for key or -1 if there is no mapping for key.
func Lookup(key string) int
```

未检查带内错误值可能导致 bug，并会把错误归咎于错误的函数。

```go
// Bad:
// The following line returns an error that Parse failed for the input value,
// whereas the failure was that there is no mapping for missingKey.
return Parse(Lookup(missingKey))
```

Go 对多返回值的支持提供了更好的方案（参见 [Effective Go section on multiple returns]）。函数不应要求调用方检查带内错误值，而应额外返回一个值，表明其他返回值是否有效。这个返回值在不需要解释时可以是 error 或布尔值，并且应当是最后一个返回值。

```go
// Good:
// Lookup returns the value for key or ok=false if there is no mapping for key.
func Lookup(key string) (value string, ok bool)
```

这个 API 防止调用方错误地写出 `Parse(Lookup(key))`，由于 `Lookup(key)` 有 2 个返回值，这样写会导致编译期错误。

以这种方式返回错误，能鼓励更健壮、更显式的错误处理：

```go
// Good:
value, ok := Lookup(key)
if !ok {
    return fmt.Errorf("no value for %q", key)
}
return Parse(value)
```

一些标准库函数，例如 `strings` 包中的那些，会返回带内错误值。这大大简化了字符串处理代码，代价是要求程序员更加勤勉。总的来说，Google 代码库中的 Go 代码应当为错误返回额外的值。

[Effective Go section on multiple returns]: http://golang.org/doc/effective_go.html#multiple-returns

<a id="indent-error-flow"></a>

### 缩进错误流程

<a id="TOC-IndentErrorFlow"></a>

在继续编写其余代码之前先处理错误。这能让读者迅速找到正常路径，从而提升代码的可读性。同样的逻辑适用于任何「先测试条件、然后以终止性语句结束」的代码块（例如 `return`、`panic`、`log.Fatal`）。

在未满足终止条件时运行的代码应当出现在 `if` 块之后，而不应缩进在 `else` 子句里。

```go
// Good:
if err != nil {
    // error handling
    return // or continue, etc.
}
// normal code
```

```go
// Bad:
if err != nil {
    // error handling
} else {
    // normal code that looks abnormal due to indentation
}
```

> **提示：** 如果某个变量的使用超过几行代码，通常就不值得使用带初始化器的 `if` 风格。这些情况下，通常更好的做法是把声明移到外面，使用标准的 `if` 语句：
>
> ```go
> // Good:
> x, err := f()
> if err != nil {
>   // error handling
>   return
> }
> // lots of code that uses x
> // across multiple lines
> ```
>
> ```go
> // Bad:
> if x, err := f(); err != nil {
>   // error handling
>   return
> } else {
>   // lots of code that uses x
>   // across multiple lines
> }
> ```

更多细节参见 [Go Tip #1: Line of Sight] 与 [TotT: Reduce Code Complexity by Reducing Nesting](https://testing.googleblog.com/2017/06/code-health-reduce-nesting-reduce.html)。

[Go Tip #1: Line of Sight]: go-style.md#gotip

<a id="language"></a>

## 语言

<a id="literal-formatting"></a>

### 字面量格式

Go 拥有异常强大的 [composite literal syntax]，借助它可以在单个表达式中表达深度嵌套的复杂值。只要可行，就应当使用这种字面量语法，而不是逐字段地构造值。`gofmt` 对字面量的格式化通常相当不错，但还是有一些额外的规则，可以让这些字面量保持可读与可维护。

[composite literal syntax]: https://golang.org/ref/spec#Composite_literals

<a id="literal-field-names"></a>

#### 字段名

结构体字面量对于定义在当前包之外的类型，必须指定**字段名**。

*   对于来自其他包的类型，要包含字段名。

    ```go
    // Good:
    // https://pkg.go.dev/encoding/csv#Reader
    r := csv.Reader{
      Comma: ',',
      Comment: '#',
      FieldsPerRecord: 4,
    }
    ```

    结构体中字段的位置以及字段的完整集合（在省略字段名时，这两者都必须写对）通常不被视为结构体公共 API 的一部分；需要指定字段名，以避免不必要的耦合。

    ```go
    // Bad:
    r := csv.Reader{',', '#', 4, false, false, false, false}
    ```

*   对于包内的类型，字段名是可选的。

    ```go
    // Good:
    okay := Type{42}
    also := internalType{4, 2}
    ```

    如果使用字段名能让代码更清晰，就仍然应当使用，而且这样做非常常见。例如，字段数量很多的结构体几乎总是应当用字段名来初始化。

<!-- TODO: 这里或许可以换一个字段更少、更好的示例。 -->

    ```go
    // Good:
    okay := StructWithLotsOfFields{
      field1: 1,
      field2: "two",
      field3: 3.14,
      field4: true,
    }
    ```

<a id="literal-matching-braces"></a>

#### 花括号配对

一对花括号的后半个应当总是与开括号出现在缩进量相同的那一行上。单行字面量必然具备这一性质。当字面量跨越多行时，保持这一性质能让字面量的花括号配对与函数、`if` 语句等常见 Go 语法结构的配对方式保持一致。

这方面最常见的错误，是在多行结构体字面量中把收尾花括号放在与某个值同一行上。这种情况下，该行应当以逗号结尾，收尾花括号应当出现在下一行。

```go
// Good:
good := []*Type{{Key: "value"}}
```

```go
// Good:
good := []*Type{
    {Key: "multi"},
    {Key: "line"},
}
```

```go
// Bad:
bad := []*Type{
    {Key: "multi"},
    {Key: "line"}}
```

```go
// Bad:
bad := []*Type{
    {
        Key: "value"},
}
```

<a id="literal-cuddled-braces"></a>

#### 紧贴的花括号

对于切片和数组字面量，仅在以下两条同时成立时，才允许去掉花括号之间的空白（也就是把它们「紧贴」在一起）。

*   [缩进一致](#literal-matching-braces)
*   内部的值也是字面量或 proto 构造器（即不是变量或其他表达式）

```go
// Good:
good := []*Type{
    { // Not cuddled
        Field: "value",
    },
    {
        Field: "value",
    },
}
```

```go
// Good:
good := []*Type{{ // Cuddled correctly
    Field: "value",
}, {
    Field: "value",
}}
```

```go
// Good:
good := []*Type{
    first, // Can't be cuddled
    {Field: "second"},
}
```

```go
// Good:
okay := []*pb.Type{pb.Type_builder{
    Field: "first", // Proto Builders may be cuddled to save vertical space
}.Build(), pb.Type_builder{
    Field: "second",
}.Build()}
```

```go
// Bad:
bad := []*Type{
    first,
    {
        Field: "second",
    }}
```

<a id="literal-repeated-type-names"></a>

#### 重复的类型名

在切片和 map 字面量中可以省略重复的类型名。这有助于减少杂乱。显式写出重复类型名的合理场合是：处理项目中不常见的复杂类型时，或者当重复的类型名位于相隔较远的行上、能够提醒读者上下文时。

```go
// Good:
good := []*Type{
    {A: 42},
    {A: 43},
}
```

```go
// Bad:
repetitive := []*Type{
    &Type{A: 42},
    &Type{A: 43},
}
```

```go
// Good:
good := map[Type1]*Type2{
    {A: 1}: {B: 2},
    {A: 3}: {B: 4},
}
```

```go
// Bad:
repetitive := map[Type1]*Type2{
    Type1{A: 1}: &Type2{B: 2},
    Type1{A: 3}: &Type2{B: 4},
}
```

**提示：**如果想去除结构体字面量中重复的类型名，可以运行 `gofmt -s`。

<a id="literal-zero-value-fields"></a>

#### 零值字段

当不会因此损失清晰度时，可以从结构体字面量中省略 [Zero-value] 字段。

设计良好的 API 常常采用零值构造来增强可读性。例如，从下面的结构体中省略三个零值字段，能让注意力集中到唯一一个被指定的选项上。

[Zero-value]: https://golang.org/ref/spec#The_zero_value

```go
// Bad:
import (
  "github.com/golang/leveldb"
  "github.com/golang/leveldb/db"
)

ldb := leveldb.Open("/my/table", &db.Options{
    BlockSize: 1<<16,
    ErrorIfDBExists: true,

    // These fields all have their zero values.
    BlockRestartInterval: 0,
    Comparer: nil,
    Compression: nil,
    FileSystem: nil,
    FilterPolicy: nil,
    MaxOpenFiles: 0,
    WriteBufferSize: 0,
    VerifyChecksums: false,
})
```

```go
// Good:
import (
  "github.com/golang/leveldb"
  "github.com/golang/leveldb/db"
)

ldb := leveldb.Open("/my/table", &db.Options{
    BlockSize: 1<<16,
    ErrorIfDBExists: true,
})
```

表驱动测试中的结构体常常受益于 [explicit field names]，尤其是当测试结构体并不简单时。这样一来，当相关字段与测试用例无关时，作者就可以完全省略取零值的字段。例如，成功的测试用例应当省略任何与错误或失败相关的字段。在零值对于理解测试用例是必要的情况下，比如测试零值输入或 `nil` 输入，则应当指定字段名。

[explicit field names]: #literal-field-names

**简洁**

```go
tests := []struct {
    input      string
    wantPieces []string
    wantErr    error
}{
    {
        input:      "1.2.3.4",
        wantPieces: []string{"1", "2", "3", "4"},
    },
    {
        input:   "hostname",
        wantErr: ErrBadHostname,
    },
}
```

**显式**

```go
tests := []struct {
    input    string
    wantIPv4 bool
    wantIPv6 bool
    wantErr  bool
}{
    {
        input:    "1.2.3.4",
        wantIPv4: true,
        wantIPv6: false,
    },
    {
        input:    "1:2::3:4",
        wantIPv4: false,
        wantIPv6: true,
    },
    {
        input:    "hostname",
        wantIPv4: false,
        wantIPv6: false,
        wantErr:  true,
    },
}
```

<a id="nil-slices"></a>

### nil 切片

在大多数用途下，`nil` 与空切片之间没有功能上的差别。像 `len` 和 `cap` 这样的内置函数作用在 `nil` 切片上时，行为符合预期。

```go
// Good:
import "fmt"

var s []int         // nil

fmt.Println(s)      // []
fmt.Println(len(s)) // 0
fmt.Println(cap(s)) // 0
for range s {...}   // no-op

s = append(s, 42)
fmt.Println(s)      // [42]
```

如果你把空切片声明为局部变量（尤其是当它可能作为返回值的来源时），优先使用 nil 初始化，以降低调用方出错的风险。

```go
// Good:
var t []string
```

```go
// Bad:
t := []string{}
```

不要创建强迫调用方区分 nil 与空切片的 API。

```go
// Good:
// Ping pings its targets.
// Returns hosts that successfully responded.
func Ping(hosts []string) ([]string, error) { ... }
```

```go
// Bad:
// Ping pings its targets and returns a list of hosts
// that successfully responded. Can be empty if the input was empty.
// nil signifies that a system error occurred.
func Ping(hosts []string) []string { ... }
```

在设计接口时，避免区分 `nil` 切片与非 `nil` 的零长度切片，因为这可能导致难以察觉的编程错误。通常的做法是用 `len` 检查是否为空，而不是用 `== nil`。

下面这个实现把 `nil` 和零长度切片都视为「空」：

```go
// Good:
// describeInts describes s with the given prefix, unless s is empty.
func describeInts(prefix string, s []int) {
    if len(s) == 0 {
        return
    }
    fmt.Println(prefix, s)
}
```

而不是把这种区分作为 API 的一部分来依赖：

```go
// Bad:
func maybeInts() []int { /* ... */ }

// describeInts describes s with the given prefix; pass nil to skip completely.
func describeInts(prefix string, s []int) {
  // The behavior of this function unintentionally changes depending on what
  // maybeInts() returns in 'empty' cases (nil or []int{}).
  if s == nil {
    return
  }
  fmt.Println(prefix, s)
}

describeInts("Here are some ints:", maybeInts())
```

进一步讨论见 [in-band errors]。

[in-band errors]: #in-band-errors

<a id="indentation-confusion"></a>

### 缩进混淆

如果换行会让该行剩余部分与缩进的代码块对齐，就应当避免引入换行。如果无法避免，请留一个空格，把块中的代码与折行后的内容分开。

```go
// Bad:
if longCondition1 && longCondition2 &&
    // Conditions 3 and 4 have the same indentation as the code within the if.
    longCondition3 && longCondition4 {
    log.Info("all conditions met")
}
```

具体准则与示例见以下几节：

*   [函数格式](#func-formatting)
*   [条件语句与循环](#conditional-formatting)
*   [字面量格式](#literal-formatting)

<a id="func-formatting"></a>

### 函数格式

函数或方法声明的签名应当保持在一行内，以避免[缩进混淆](#indentation-confusion)。

函数的参数列表可能构成 Go 源文件中最长的一些行。但参数列表之后紧跟着缩进的变化，因此很难在不使后续行看起来令人困惑地像是函数体一部分的前提下断行：

```go
// Bad:
func (r *SomeType) SomeLongFunctionName(foo1, foo2, foo3 string,
    foo4, foo5, foo6 int) {
    foo7 := bar(foo1)
    // ...
}
```

关于如何缩短原本会有很多参数的函数调用点，见[最佳实践](go-best-practices.md#funcargs)中的几种做法。

把局部变量提取出来，往往就能缩短行。

```go
// Good:
local := helper(some, parameters, here)
good := foo.Call(list, of, parameters, local)
```

同样，函数和方法调用不应当仅仅根据行长来断行。

```go
// Good:
good := foo.Call(long, list, of, parameters, all, on, one, line)
```

```go
// Bad:
bad := foo.Call(long, list, of, parameters,
    with, arbitrary, line, breaks)
```

尽可能避免为特定的函数参数添加行内注释。应当改用[选项结构体](go-best-practices.md#option-structure)，或者为函数文档补充更多细节。

```go
// Good:
good := server.New(ctx, server.Options{Port: 42})
```

```go
// Bad:
bad := server.New(
    ctx,
    42, // Port
)
```

如果 API 无法更改，或者本地调用情况特殊（无论这次调用是否过长），只要有助于理解这次调用，就总是可以添加换行。

```go
// Good:
canvas.RenderHeptagon(fillColor,
    x0, y0, vertexColor0,
    x1, y1, vertexColor1,
    x2, y2, vertexColor2,
    x3, y3, vertexColor3,
    x4, y4, vertexColor4,
    x5, y5, vertexColor5,
    x6, y6, vertexColor6,
)
```

注意，上面示例中的各行并不是在某个特定列边界处折行，而是根据顶点坐标与颜色来分组。

函数内的长字符串字面量不应当为了行长而断开。对于包含这类字符串的函数，可以在字符串格式之后添加换行，把参数放在下一行或后续几行上。关于换行位置的决定，最好基于输入的语义分组，而不是纯粹依据行长。

```go
// Good:
log.Warningf("Database key (%q, %d, %q) incompatible in transaction started by (%q, %d, %q)",
    currentCustomer, currentOffset, currentKey,
    txCustomer, txOffset, txKey)
```

```go
// Bad:
log.Warningf("Database key (%q, %d, %q) incompatible in"+
    " transaction started by (%q, %d, %q)",
    currentCustomer, currentOffset, currentKey, txCustomer,
    txOffset, txKey)
```

<a id="conditional-formatting"></a>

### 条件语句与循环

`if` 语句不应当断行；多行的 `if` 子句可能导致[缩进混淆](#indentation-confusion)。

```go
// Bad:
// The second if statement is aligned with the code within the if block, causing
// indentation confusion.
if db.CurrentStatusIs(db.InTransaction) &&
    db.ValuesEqual(db.TransactionKey(), row.Key()) {
    return db.Errorf(db.TransactionError, "query failed: row (%v): key does not match transaction key", row)
}
```

如果不需要短路行为，可以直接把布尔操作数提取出来：

```go
// Good:
inTransaction := db.CurrentStatusIs(db.InTransaction)
keysMatch := db.ValuesEqual(db.TransactionKey(), row.Key())
if inTransaction && keysMatch {
    return db.Error(db.TransactionError, "query failed: row (%v): key does not match transaction key", row)
}
```

可能还有其他可以提取的局部变量，尤其是当条件式本身已经重复时：

```go
// Good:
uid := user.GetUniqueUserID()
if db.UserIsAdmin(uid) || db.UserHasPermission(uid, perms.ViewServerConfig) || db.UserHasPermission(uid, perms.CreateGroup) {
    // ...
}
```

```go
// Bad:
if db.UserIsAdmin(user.GetUniqueUserID()) || db.UserHasPermission(user.GetUniqueUserID(), perms.ViewServerConfig) || db.UserHasPermission(user.GetUniqueUserID(), perms.CreateGroup) {
    // ...
}
```

包含闭包或多行结构体字面量的 `if` 语句，应当确保[花括号配对](#literal-matching-braces)，以避免[缩进混淆](#indentation-confusion)。

```go
// Good:
if err := db.RunInTransaction(func(tx *db.TX) error {
    return tx.Execute(userUpdate, x, y, z)
}); err != nil {
    return fmt.Errorf("user update failed: %s", err)
}
```

```go
// Good:
if _, err := client.Update(ctx, &upb.UserUpdateRequest{
    ID:   userID,
    User: user,
}); err != nil {
    return fmt.Errorf("user update failed: %s", err)
}
```

同样，不要试图往 `for` 语句里插入人为的换行。如果没有优雅的重构方式，就干脆让这一行保持很长：

```go
// Good:
for i, max := 0, collection.Size(); i < max && !collection.HasPendingWriters(); i++ {
    // ...
}
```

不过，往往是有办法的：

```go
// Good:
for i, max := 0, collection.Size(); i < max; i++ {
    if collection.HasPendingWriters() {
        break
    }
    // ...
}
```

`switch` 和 `case` 语句也应当保持在一行内。

```go
// Good:
switch good := db.TransactionStatus(); good {
case db.TransactionStarting, db.TransactionActive, db.TransactionWaiting:
    // ...
case db.TransactionCommitted, db.NoTransaction:
    // ...
default:
    // ...
}
```

```go
// Bad:
switch bad := db.TransactionStatus(); bad {
case db.TransactionStarting,
    db.TransactionActive,
    db.TransactionWaiting:
    // ...
case db.TransactionCommitted,
    db.NoTransaction:
    // ...
default:
    // ...
}
```

如果这一行过长，就把所有 `case` 缩进，并用空行把它们隔开，以避免[缩进混淆](#indentation-confusion)：

```go
// Good:
switch db.TransactionStatus() {
case
    db.TransactionStarting,
    db.TransactionActive,
    db.TransactionWaiting,
    db.TransactionCommitted:

    // ...
case db.NoTransaction:
    // ...
default:
    // ...
}
```

在比较变量与常量的条件式中，把变量值放在相等运算符的左侧：

```go
// Good:
if result == "foo" {
  // ...
}
```

而不要用常量在前这种不够清晰的写法（[「Yoda 风格条件式」](https://en.wikipedia.org/wiki/Yoda_conditions)）：

```go
// Bad:
if "foo" == result {
  // ...
}
```

<a id="copying"></a>

### 复制

<a id="TOC-Copying"></a>

为避免意外的别名与类似 bug，从其他包复制结构体时要小心。例如 `sync.Mutex` 这类同步对象不得被复制。

`bytes.Buffer` 类型包含一个 `[]byte` 切片，以及（作为对小字符串的优化）一个该切片可能指向的小字节数组。如果你复制一个 `Buffer`，副本中的切片可能与原值中的数组互为别名，导致后续的方法调用产生令人意外的效果。

一般而言，如果类型 `T` 的方法关联在指针类型 `*T` 上，就不要复制 `T` 的值。

```go
// Bad:
b1 := bytes.Buffer{}
b2 := b1
```

调用接收值接收者的方法会隐藏这次复制。在编写 API 时，如果你的结构体含有不应被复制的字段，通常应当接收并返回指针类型。

以下这些是可以接受的：

```go
// Good:
type Record struct {
  buf bytes.Buffer
  // other fields omitted
}

func New() *Record {...}

func (r *Record) Process(...) {...}

func Consumer(r *Record) {...}
```

但以下这些通常是错误的：

```go
// Bad:
type Record struct {
  buf bytes.Buffer
  // other fields omitted
}


func (r Record) Process(...) {...} // Makes a copy of r.buf

func Consumer(r Record) {...} // Makes a copy of r.buf
```

这条建议同样适用于复制 `sync.Mutex`。

<a id="dont-panic"></a>

### 不要 panic

<a id="TOC-Don-t-Panic"></a>

不要用 `panic` 做常规的错误处理。应当改用 `error` 和多个返回值。参见 [Effective Go section on errors]。

在 `package main` 和初始化代码中，对于应当终止程序的错误（例如无效配置），可以考虑使用 [`log.Exit`]，因为在这类情形下栈回溯通常对读者没有帮助。请注意 [`log.Exit`] 会调用 [`os.Exit`]，任何被 defer 的函数都不会被执行。

对于表示「不可能」情形的错误，也就是本应在代码评审和/或测试中总能被发现的 bug，函数返回错误或调用 [`log.Fatal`] 都是合理的。

另见[何时可以 panic](go-best-practices.md#when-to-panic)。

**注意：** `log.Fatalf` 不是标准库的 log。参见 [#logging]。

[Effective Go section on errors]: http://golang.org/doc/effective_go.html#errors
[`os.Exit`]: https://pkg.go.dev/os#Exit

<a id="must-functions"></a>

### Must 函数

在失败时终止程序的初始化辅助函数遵循 `MustXYZ`（或 `mustXYZ`）的命名约定。一般而言，它们只应在程序启动早期调用，而不应用在诸如用户输入这类更倾向于常规 Go 错误处理的地方。

这种情况常见于那些仅在[包初始化时](https://golang.org/ref/spec#Package_initialization)被调用、用于初始化包级变量的函数（例如 [template.Must](https://golang.org/pkg/text/template/#Must) 和 [regexp.MustCompile](https://golang.org/pkg/regexp/#MustCompile)）。

```go
// Good:
func MustParse(version string) *Version {
    v, err := Parse(version)
    if err != nil {
        panic(fmt.Sprintf("MustParse(%q) = _, %v", version, err))
    }
    return v
}

// Package level "constant". If we wanted to use `Parse`, we would have had to
// set the value in `init`.
var DefaultVersion = MustParse("1.2.3")
```

同一约定也可以用在只终止当前测试（使用 `t.Fatal`）的测试辅助函数中。这类辅助函数在构造测试值（例如[表驱动测试](#table-driven-tests)的结构体字段）时通常很方便，因为返回错误的函数无法直接赋给结构体字段。

```go
// Good:
func mustMarshalAny(t *testing.T, m proto.Message) *anypb.Any {
  t.Helper()
  any, err := anypb.New(m)
  if err != nil {
    t.Fatalf("mustMarshalAny(t, m) = %v; want %v", err, nil)
  }
  return any
}

func TestCreateObject(t *testing.T) {
  tests := []struct{
    desc string
    data *anypb.Any
  }{
    {
      desc: "my test case",
      // Creating values directly within table driven test cases.
      data: mustMarshalAny(t, mypb.Object{}),
    },
    // ...
  }
  // ...
}
```

在这两种情形中，该模式的价值在于辅助函数可以在「值」上下文中被调用。不应在难以确保错误会被捕获的地方、或在错误本应被[检查](#handle-errors)的上下文中（例如许多请求处理函数里）调用这些辅助函数。对于常量输入，这让测试可以轻松确保 `Must` 的参数格式良好；对于非常量输入，则让测试可以验证错误被[妥善处理或传播](go-best-practices.md#error-handling)。

在测试中使用 `Must` 函数时，通常应将其[标记为测试辅助函数](#mark-test-helpers)并在出错时调用 `t.Fatal`（关于这样做的更多考量，参见[测试辅助函数中的错误处理](go-best-practices.md#test-helper-error-handling)）。

当可以进行[常规错误处理](go-best-practices.md#error-handling)（包括经过一些重构）时，不应使用它们：

```go
// Bad:
func Version(o *servicepb.Object) (*version.Version, error) {
    // Return error instead of using Must functions.
    v := version.MustParse(o.GetVersionString())
    return dealiasVersion(v)
}
```

<a id="goroutine-lifetimes"></a>

### goroutine 的生命周期

<a id="TOC-GoroutineLifetimes"></a>

启动 goroutine 时，要明确它们何时退出、或者是否退出。

goroutine 可能因阻塞在通道发送或接收上而泄漏。即使没有其他 goroutine 引用该通道，垃圾回收器也不会终止阻塞在通道上的 goroutine。

即使 goroutine 没有泄漏，在不再需要它们时仍让其处于运行中，也会造成其他微妙且难以诊断的问题。向已关闭的通道发送会引发 panic。

```go
// Bad:
ch := make(chan int)
ch <- 42
close(ch)
ch <- 13 // panic
```

在「结果已不再需要」之后修改仍在使用的输入，可能导致数据竞争。让 goroutine 长时间处于运行中，可能导致不可预测的内存占用。

并发代码应当写得让 goroutine 的生命周期一目了然。通常这意味着把与同步相关的代码限制在函数作用域内，并把逻辑抽离成 [synchronous functions]。如果并发性仍不明显，那么记录 goroutine 何时退出以及为何退出就很重要。

围绕 context 用法遵循最佳实践的代码通常有助于让这一点变得清晰。按惯例，它由 [`context.Context`] 管理：

```go
// Good:
func (w *Worker) Run(ctx context.Context) error {
    var wg sync.WaitGroup
    // ...
    for item := range w.q {
        // process returns at latest when the context is cancelled.
        wg.Add(1)
        go func() {
            defer wg.Done()
            process(ctx, item)
        }()
    }
    // ...
    wg.Wait()  // Prevent spawned goroutines from outliving this function.
}
```

上述做法还有其他变体，例如使用 `chan struct{}` 这类原始信号通道、同步变量、[条件变量][rethinking-slides]等等。重要的是 goroutine 的结束对后续维护者而言是显而易见的。

相比之下，下面的代码对其派生的 goroutine 何时结束漠不关心：

```go
// Bad:
func (w *Worker) Run() {
    // ...
    for item := range w.q {
        // process returns when it finishes, if ever, possibly not cleanly
        // handling a state transition or termination of the Go program itself.
        go process(item)
    }
    // ...
}
```

这段代码看起来可能没问题，但存在若干深层问题：

*   在生产环境中，这段代码可能存在未定义行为，程序可能无法干净地终止，即使操作系统释放了资源。

*   由于代码的生命周期不确定，很难对它进行有意义的测试。

*   如上所述，这段代码可能会泄漏资源。

另见：

*   [Never start a goroutine without knowing how it will stop][cheney-stop]
*   重新思考经典并发模式，Bryan Mills 的演讲：[幻灯片][rethinking-slides]、[视频][rethinking-video]
*   [When Go programs end]
*   [Documentation Conventions: Contexts]

[synchronous functions]: #synchronous-functions
[cheney-stop]: https://dave.cheney.net/2016/12/22/never-start-a-goroutine-without-knowing-how-it-will-stop
[rethinking-slides]: https://drive.google.com/file/d/1nPdvhB0PutEJzdCq5ms6UI58dp50fcAN/view
[rethinking-video]: https://www.youtube.com/watch?v=5zXAHh5tJqQ
[When Go programs end]: https://changelog.com/gotime/165
[Documentation Conventions: Contexts]: go-best-practices.md#documentation-conventions-contexts

<a id="interfaces"></a>

### 接口

<a id="TOC-Interfaces"></a>

在存在[真实需求](go-style.md#simplicity)之前，避免创建接口。关注所需的行为，而不是仅仅关注诸如「service」或「repository」之类抽象的命名模式。

*   不要仅仅为了抽象或测试，就把 RPC 客户端包装进新的手工接口。应当改用[真实传输](go-best-practices.md#use-real-transports)（[testing RPC]）。

*   不要仅为测试而定义后门，或导出接口的 [test double] 实现。应当优先通过真实实现的 [public API] 进行测试。

设计接口时应保持小规模，以便于实现和组合（[GoTip #78: Minimal Viable Interfaces]）。恰当地为接口编写文档，包括其契约、边界情况和预期错误。如果接口类型仅在包内部使用，就保持其未导出。

接口的消费方应当定义接口（而不是实现接口的包），确保接口只包含他们实际使用的方法。如果接口本身就是产品（一种通用协议），那么提供方包可以导出该接口，以防接口被重复定义的膨胀。

有句格言：函数应当接收接口作为参数，但返回具体类型（[GoTip #49: Accept Interfaces, Return Concrete Types]）。返回具体类型让调用方可以访问该具体实现的所有公开方法和字段，而不只是预先选定的接口中定义的那部分方法。调用方仍然可以把该具体结果传给任何期望接口的其他函数。有时为了封装，返回接口是可以接受的（例如 `error` 接口），某些构造也是如此，比如命令模式、链式调用、工厂模式，以及[策略](https://en.wikipedia.org/wiki/Strategy_pattern)模式。

关于接口更深入的讨论见[最佳实践中关于接口的章节](go-best-practices.md#interfaces)。

[GoTip #78: Minimal Viable Interfaces]: go-style.md#gotip
[GoTip #49: Accept Interfaces, Return Concrete Types]: go-style.md#gotip
[testing RPC]: https://codelabs.developers.google.com/grpc/getting-started-grpc-go#3
[test double]: https://abseil.io/resources/swe-book/html/ch13.html
[public API]: https://abseil.io/resources/swe-book/html/ch12.html#test_via_public_apis

<a id="generics"></a>

### 泛型

泛型（正式名称为「[Type Parameters]」）在满足你的业务需求时是允许使用的。在许多应用中，使用现有语言特性（切片、映射、接口等）的常规做法同样有效，且没有额外的复杂性，因此要警惕过早使用。参见关于[最小机制](go-style.md#least-mechanism)的讨论。

引入使用泛型的导出 API 时，要确保它得到了恰当的文档说明。强烈鼓励附上能说明动机的可运行 [examples]。

不要仅仅因为你正在实现一个不关心成员元素类型的算法或数据结构就使用泛型。如果在实践中只会实例化一种类型，那就先让代码在不使用泛型的情况下针对该类型工作。与移除事后发现不必要的抽象相比，之后再添加多态会容易得多。

不要用泛型来发明领域特定语言（DSL）。尤其要避免引入可能给读者带来沉重负担的错误处理框架。应当优先采用既有的[错误处理](#errors)实践。对于测试，要格外警惕引入[断言库](#assert)或框架，那会导致[测试失败信息](#useful-test-failures)用处更小。

总体而言：

*   [Write code, don't design types]。出自 Robert Griesemer 和 Ian Lance Taylor 在 GopherCon 的演讲。
*   如果你有若干类型共享一个有用的统一接口，考虑用该接口来建模解决方案。可能并不需要泛型。
*   否则，与其依赖 `any` 类型和过度的[类型 switch](https://tour.golang.org/methods/16)，不如考虑泛型。

另见：

*   [Using Generics in Go]，Ian Lance Taylor 的演讲

*   Go 官网上的[泛型教程]

[Generics tutorial]: https://go.dev/doc/tutorial/generics
[Type Parameters]: https://go.dev/design/43651-type-parameters
[Using Generics in Go]: https://www.youtube.com/watch?v=nr8EpUO9jhw
[Write code, don't design types]: https://www.youtube.com/watch?v=Pa_e9EeCdy8&t=1250s

<a id="pass-values"></a>

### 传值

<a id="TOC-PassValues"></a>

不要仅仅为了省几个字节就把指针作为函数参数传递。如果函数在其内部只以 `*x` 的形式读取参数 `x`，那么该参数就不应该是指针。常见的例子包括传递指向字符串的指针（`*string`）或指向接口值的指针（`*io.Reader`）。这两种情况下，值本身都是固定大小的，可以直接传递。

这条建议不适用于大型结构体，甚至也不适用于那些可能变大的小型结构体。特别地，protocol buffer 消息通常应按指针而非按值处理。指针类型满足 `proto.Message` 接口（`proto.Marshal`、`protocmp.Transform` 等接受该接口），而且 protocol buffer 消息可能相当大，并且往往随时间推移变得更大。

<a id="receiver-type"></a>

### 接收者类型

<a id="TOC-ReceiverType"></a>

[method receiver] 既可以按值传递，也可以按指针传递，就像普通函数参数一样。两者之间的选择取决于该方法应当属于哪个 [method set(s)]。

[method receiver]: https://golang.org/ref/spec#Method_declarations
[method set(s)]: https://golang.org/ref/spec#Method_sets

**正确性优先于速度或简洁。** 有些情况下你必须使用指针值。其他情况下，对于大型类型，或者在你对代码将如何增长没有很好把握时出于前瞻性考虑，选择指针；而对于简单的 [plain old data]，使用值。

下面列出每种情况的进一步细节：

*   如果接收者是切片，且方法不会对切片重新切片或重新分配，那么使用值而不是指针。

    ```go
    // Good:
    type Buffer []byte

    func (b Buffer) Len() int { return len(b) }
    ```

*   如果方法需要修改接收者，那么接收者必须是指针。

    ```go
    // Good:
    type Counter int

    func (c *Counter) Inc() { *c++ }

    // See https://pkg.go.dev/container/heap.
    type Queue []Item

    func (q *Queue) Push(x Item) { *q = append([]Item{x}, *q...) }
    ```

*   如果接收者是含有[无法安全复制](#copying)字段的结构体，则使用指针接收者。常见的例子是 [`sync.Mutex`] 和其他同步类型。

    ```go
    // Good:
    type Counter struct {
        mu    sync.Mutex
        total int
    }

    func (c *Counter) Inc() {
        c.mu.Lock()
        defer c.mu.Unlock()
        c.total++
    }
    ```

    **提示：** 查看该类型的 [Godoc] 以了解复制它是否安全。

*   如果接收者是「大型」结构体或数组，指针接收者可能更高效。传递结构体等价于把它的所有字段或元素作为参数传给方法。如果这看起来大到不宜[按值传递](#pass-values)，那么指针是个好选择。

*   对于会与修改接收者的其他函数并发调用或并发运行的方法：如果那些修改不应对你的方法可见，就使用值；否则使用指针。

*   如果接收者是结构体或数组，且其中任一元素是指向可能被修改之物的指针，那么优先使用指针接收者，以便让读者清楚其可变意图。

    ```go
    // Good:
    type Counter struct {
        m *Metric
    }

    func (c *Counter) Inc() {
        c.m.Add(1)
    }
    ```

*   如果接收者是 [built-in type]，例如整数或字符串，且不需要被修改，那么使用值。

    ```go
    // Good:
    type User string

    func (u User) String() { return string(u) }
    ```

*   如果接收者是映射、函数或通道，使用值而不是指针。

    ```go
    // Good:
    // See https://pkg.go.dev/net/http#Header.
    type Header map[string][]string

    func (h Header) Add(key, value string) { /* omitted */ }
    ```

*   如果接收者是「小型」数组或结构体，且它天然是值类型、没有可变字段也没有指针，那么值接收者通常是正确选择。

    ```go
    // Good:
    // See https://pkg.go.dev/time#Time.
    type Time struct { /* omitted */ }

    func (t Time) Add(d Duration) Time { /* omitted */ }
    ```

*   拿不准时，使用指针接收者。

作为一般准则，一个类型的方法要么全是指针方法，要么全是值方法。

**注意：** 关于向函数传值还是传指针是否影响性能，存在大量错误信息。编译器既可以选择在栈上传递指向值的指针，也可以选择在栈上复制值，但在多数情况下，这些考量不应压过代码的可读性与正确性。当性能确实重要时，重要的是先用贴近实际的基准测试对两种做法做性能分析，再断定哪一种做法更优。

[plain old data]: https://en.wikipedia.org/wiki/Passive_data_structure
[`sync.Mutex`]: https://pkg.go.dev/sync#Mutex
[built-in type]: https://pkg.go.dev/builtin

<a id="switch-break"></a>

### `switch` 与 `break`

<a id="TOC-SwitchBreak"></a>

不要在 `switch` 子句末尾使用没有目标标签的 `break` 语句；它们是冗余的。与 C 和 Java 不同，Go 中的 `switch` 子句会自动 break，要实现 C 风格的行为需要使用 `fallthrough` 语句。如果你想说明空子句的用途，应当用注释而不是 `break`。

```go
// Good:
switch x {
case "A", "B":
    buf.WriteString(x)
case "C":
    // handled outside of the switch statement
default:
    return fmt.Errorf("unknown value: %q", x)
}
```

```go
// Bad:
switch x {
case "A", "B":
    buf.WriteString(x)
    break // this break is redundant
case "C":
    break // this break is redundant
default:
    return fmt.Errorf("unknown value: %q", x)
}
```

> **注意：** 如果 `switch` 子句位于 `for` 循环内，在 `switch` 里使用 `break` 并不会退出外层的 `for` 循环。
>
> ```go
> for {
>   switch x {
>   case "A":
>      break // exits the switch, not the loop
>   }
> }
> ```
>
> 要跳出外层循环，请在 `for` 语句上使用标签：
>
> ```go
> loop:
>   for {
>     switch x {
>     case "A":
>        break loop // exits the loop
>     }
>   }
> ```

<a id="synchronous-functions"></a>

### 同步函数

<a id="TOC-SynchronousFunctions"></a>

同步函数直接返回其结果，并在返回前完成所有回调或通道操作。相对于异步函数，应优先使用同步函数。

同步函数把 goroutine 限制在一次调用之内。这有助于推断它们的生命周期，并避免泄漏和数据竞争。同步函数也更容易测试，因为调用方可以传入输入并检查输出，无需轮询或同步。

如有必要，调用方可以通过在单独的 goroutine 中调用该函数来添加并发。然而，要在调用方一侧移除不必要的并发是相当困难的（有时甚至不可能）。

另见：

*   重新思考经典并发模式，Bryan Mills 的演讲：[幻灯片][rethinking-slides]、[视频][rethinking-video]

<a id="type-aliases"></a>

### 类型别名

<a id="TOC-TypeAliases"></a>

使用*类型定义* `type T1 T2` 来定义新类型。使用 [*type alias*] `type T1 = T2` 来引用既有类型而不定义新类型。类型别名很少见；它的主要用途是帮助把包迁移到新的源码位置。不需要时不要使用类型别名。

[*type alias*]: http://golang.org/ref/spec#Type_declarations

<a id="use-percent-q"></a>

### 使用 %q

<a id="TOC-UsePercentQ"></a>

Go 的格式化函数（`fmt.Printf` 等）有一个 `%q` 动词，它会把字符串打印在双引号内。

```go
// Good:
fmt.Printf("value %q looks like English text", someText)
```

相对于用 `%s` 手工实现等价效果，优先使用 `%q`：

```go
// Bad:
fmt.Printf("value \"%s\" looks like English text", someText)
// Avoid manually wrapping strings with single-quotes too:
fmt.Printf("value '%s' looks like English text", someText)
```

在面向人的输出中，如果输入值可能为空或含有控制字符，建议使用 `%q`。悄无声息的空字符串很难被注意到，而 `""` 能清楚地表明这一点。

<a id="use-any"></a>

### 使用 any

Go 1.18 引入了 `any` 类型，作为 `interface{}` 的 [alias]。因为它是别名，`any` 在许多情形下等同于 `interface{}`，在其他情形下也可以通过显式转换轻松互换。新代码中优先使用 `any`。

[alias]: https://go.googlesource.com/proposal/+/master/design/18130-type-alias.md

## 常用库

<a id="flags"></a>

### 命令行标志

<a id="TOC-Flags"></a>

Google 代码库中的 Go 程序使用 [standard `flag` package] 的内部变体。它的接口类似，但能与 Google 内部系统良好互操作。Go 二进制文件中的标志名应优先使用下划线分隔单词，而保存标志值的变量则应遵循标准 Go 命名风格（[mixed caps]）。具体来说，标志名应采用 snake case，变量名则应是等价的 camel case 名。

```go
// Good:
var (
    pollInterval = flag.Duration("poll_interval", time.Minute, "Interval to use for polling.")
)
```

```go
// Bad:
var (
    poll_interval = flag.Int("pollIntervalSeconds", 60, "Interval to use for polling in seconds.")
)
```

标志只能定义在 `package main` 或等价的位置。

通用包应当通过 Go API 配置，而不是打穿到命令行接口；不要让导入一个库成为导出新标志的副作用。也就是说，优先使用显式的函数参数或结构体字段赋值，或者在远为少见且经过最严格审视的情况下，使用导出的全局变量。在极少数确有必要打破此规则的场合，标志名必须清楚地表明它配置的是哪个包。

如果你的标志是全局变量，把它们放在它们自己的 `var` 分组中，位于 imports 段之后。

关于创建带子命令的 [complex CLIs] 的最佳实践，还有额外的讨论。

另见：

*   [每周提示 #45：避免使用标志，尤其是在库代码中][totw-45]
*   [Go Tip #10：配置结构体与标志](go-style.md#gotip)
*   [Go Tip #80：依赖注入原则](go-style.md#gotip)

[standard `flag` package]: https://golang.org/pkg/flag/
[mixed caps]: go-style.md#mixed-caps
[complex CLIs]: go-best-practices.md#complex-clis
[totw-45]: https://abseil.io/tips/45

<a id="logging"></a>

### 日志

Google 代码库中的 Go 程序使用标准 [`log`] 包的一个变体。它的接口与标准库相似但更强大，并且能与 Google 内部系统良好互操作。该库有一个开源版本，即 [`glog` 包]，开源 Google 项目可以使用它，但本指南全篇都称其为 `log`。

**注意：** 对于异常的程序退出，该库使用 `log.Fatal` 中止并附带堆栈跟踪，使用 `log.Exit` 中止但不附带堆栈跟踪。标准库中那样的 `log.Panic` 函数在这里并不存在。

**提示：** `log.Info(v)` 等价于 `log.Infof("%v", v)`，其他日志级别同理。没有格式化要做时，优先使用非格式化版本。

参见：

*   关于[记录日志错误](go-best-practices.md#error-logging)和[自定义详细程度级别](go-best-practices.md#vlog)的最佳实践
*   何时以及如何使用 log 包来[停止程序](go-best-practices.md#checks-and-panics)

[`log`]: https://pkg.go.dev/log
[`log/slog`]: https://pkg.go.dev/log/slog
[package `glog`]: https://pkg.go.dev/github.com/golang/glog
[`log.Exit`]: https://pkg.go.dev/github.com/golang/glog#Exit
[`log.Fatal`]: https://pkg.go.dev/github.com/golang/glog#Fatal

<a id="contexts"></a>

### 上下文

<a id="TOC-Contexts"></a>

[`context.Context`] 类型的值携带安全凭据、跟踪信息、截止时间和取消信号，跨越 API 与进程边界。与 Google 代码库中使用线程本地存储的 C++ 和 Java 不同，Go 程序沿着整条函数调用链显式地传递上下文，从传入的 RPC 和 HTTP 请求一直到发出的请求。

[`context.Context`]: https://pkg.go.dev/context

传给函数或方法时，[`context.Context`] 始终是第一个参数。

```go
func F(ctx context.Context /* other arguments */) {}
```

例外情况有：

*   在 HTTP 处理程序中，上下文来自 [`req.Context()`](https://pkg.go.dev/net/http#Request.Context)。
*   在流式 RPC 方法中，上下文来自流。

    使用 gRPC 流式的代码从生成的服务器类型中的一个 `Context()` 方法获取上下文，该类型实现了 `grpc.ServerStream`。参见 [gRPC 生成代码文档](https://grpc.io/docs/languages/go/generated-code/)。

*   在测试函数中（例如 `TestXXX`、`BenchmarkXXX`、`FuzzXXX`），上下文来自 [`(testing.TB).Context()`](https://pkg.go.dev/testing#TB.Context)。

*   在其他入口点函数中（这类函数的例子见下文），使用 [`context.Background()`]。

    *   在二进制目标中：`main`
    *   在通用代码和库中：`init`

> **注意**：调用链中途的代码需要用 [`context.Background()`] 创建自己的基础上下文，这种情况非常罕见。始终优先从调用方获取上下文，除非那不是正确的上下文。
>
> 你可能会遇到一些服务器库（Google 的 Go 服务器框架中 Stubby、gRPC 或 HTTP 的实现），它们为每个请求构造一个新的上下文对象。这些上下文会立即被来自传入请求的信息填充，因此当它们被传给请求处理程序时，上下文附带的值已经从客户端调用方跨越网络边界传播到了它上面。此外，这些上下文的生命周期被限定在该请求的范围内：请求结束时，上下文会被取消。
>
> 除非你正在实现一个服务器框架，否则不应该在库代码中用 [`context.Background()`] 创建上下文。相反，如果已有可用的上下文，优先使用下文提到的上下文分离。如果你认为在入口点函数之外确实需要 [`context.Background()`]，那么在着手实现之前，先与 Google Go 风格邮件列表讨论。

函数中 [`context.Context`] 排在首位的约定同样适用于测试辅助函数。

```go
// Good:
func readTestFile(ctx context.Context, t *testing.T, path string) string {}
```

不要给结构体类型添加 context 成员。相反，给该类型上每个需要传递它的方法添加一个 context 参数。唯一的例外是签名必须匹配标准库中、或 Google 无法控制的第三方库中某个接口的方法。这种情况非常罕见，在实现和可读性评审之前，应先与 Google Go 风格邮件列表讨论。

**注意：** Go 1.24 添加了 [`(testing.TB).Context()`] 方法。在测试中，优先使用 [`(testing.TB).Context()`] 而非 [`context.Background()`] 来提供测试所用的初始 [`context.Context`]。从测试函数体调用的辅助函数、环境或测试替身设置，以及其他需要上下文的函数，都应显式地传入一个上下文。

[`(testing.TB).Context()`]: https://pkg.go.dev/testing#TB.Context

Google 代码库中必须启动在父上下文被取消后仍可运行的后台操作的代码，可以使用一个内部包来做分离。关于开源替代方案的讨论，请关注 [issue #40221](https://github.com/golang/go/issues/40221)。

由于上下文是不可变的，把同一个上下文传给共享相同截止时间、取消信号、凭据、父跟踪等的多次调用是没问题的。

参见：

*   [Contexts and structs]

[`context.Background()`]: https://pkg.go.dev/context/#Background
[Contexts and structs]: https://go.dev/blog/context-and-structs

<a id="custom-contexts"></a>

#### 自定义上下文

不要在函数签名中创建自定义上下文类型，也不要使用 [`context.Context`] 之外的接口。这条规则没有例外。

想象一下，如果每个团队都有一个自定义的上下文。那么从包 `p` 到包 `q` 的每一次函数调用，都必须确定如何把 `p.Context` 转换成 `q.Context`，这对所有包对 `p` 和 `q` 都适用。这对人来说既不切实际又容易出错，而且会让添加上下文参数的自动化重构几乎无法进行。

如果你有应用数据要传递，就把它放进参数、接收者、全局变量，或者，如果它确实属于那里，放进一个 `Context` 值中。创建你自己的上下文类型是不可接受的，因为这会削弱 Go 团队让 Go 程序在生产环境中正常工作的能力。

<a id="crypto-rand"></a>

### crypto/rand

<a id="TOC-CryptoRand"></a>

不要用 `math/rand` 包生成密钥，即使是只用一次的也不行。如果未播种，生成器完全可预测。即使用 `time.Nanoseconds()` 播种，也只有几个比特的熵。相反，使用 `crypto/rand` 的 Reader，如果需要文本，就打印成十六进制或 base64。

```go
// Good:
import (
    "crypto/rand"
    // "encoding/base64"
    // "encoding/hex"
    "fmt"

    // ...
)

func Key() string {
    buf := make([]byte, 16)
    if _, err := rand.Read(buf); err != nil {
        log.Fatalf("Out of randomness, should never happen: %v", err)
    }
    return fmt.Sprintf("%x", buf)
    // or hex.EncodeToString(buf)
    // or base64.StdEncoding.EncodeToString(buf)
}
```

**注意：** `log.Fatalf` 不是标准库的 log。参见 [#logging]。

<a id="useful-test-failures"></a>

## 有用的测试失败信息

<a id="TOC-UsefulTestFailures"></a>

应当能够在不阅读测试源码的情况下诊断出测试失败的原因。测试失败时应给出有用的消息，详细说明：

*   失败是由什么引起的
*   哪些输入导致了错误
*   实际结果
*   期望是什么

为达成这一目标的具体约定见下文。

<a id="assert"></a>

### 断言库

<a id="TOC-Assert"></a>

不要把「断言库」创建为测试的辅助工具。

断言库是试图把失败消息的校验与生成合并在测试内的库（尽管同样的陷阱也可能适用于其他测试辅助函数）。关于测试辅助函数与断言库之间区别的更多内容，参见[最佳实践](go-best-practices.md#test-functions)。

```go
// Bad:
var obj BlogPost

assert.IsNotNil(t, "obj", obj)
assert.StringEq(t, "obj.Type", obj.Type, "blogPost")
assert.IntEq(t, "obj.Comments", obj.Comments, 2)
assert.StringNotEq(t, "obj.Body", obj.Body, "")
```

断言库往往会要么过早停止测试（如果 `assert` 调用 `t.Fatalf` 或 `panic`），要么省略关于测试做对了什么的相关信息：

```go
// Bad:
package assert

func IsNotNil(t *testing.T, name string, val any) {
    if val == nil {
        t.Fatalf("Data %s = nil, want not nil", name)
    }
}

func StringEq(t *testing.T, name, got, want string) {
    if got != want {
        t.Fatalf("Data %s = %q, want %q", name, got, want)
    }
}
```

复杂的断言函数往往无法提供[有用的失败消息]以及测试函数中已有的上下文。过多的断言函数和断言库会导致割裂的开发者体验：我该用哪个断言库，它应当输出什么风格的格式，等等？这种割裂会产生不必要的困惑，对于库维护者和大规模变更的作者尤其如此，因为他们负责修复潜在的下游破坏。与其为测试创建领域特定语言，不如使用 Go 本身。

断言库常常把比较和相等性检查抽出去。优先使用诸如 [`cmp`] 和 [`fmt`] 这样的标准库：

```go
// Good:
var got BlogPost

want := BlogPost{
    Comments: 2,
    Body:     "Hello, world!",
}

if !cmp.Equal(got, want) {
    t.Errorf("Blog post = %v, want = %v", got, want)
}
```

对于更领域特定的比较辅助函数，优先返回一个可以用于测试失败消息的值或错误，而不是传入 `*testing.T` 并调用它的错误报告方法：

```go
// Good:
func postLength(p BlogPost) int { return len(p.Body) }

func TestBlogPost_VeritableRant(t *testing.T) {
    post := BlogPost{Body: "I am Gunnery Sergeant Hartman, your senior drill instructor."}

    if got, want := postLength(post), 60; got != want {
        t.Errorf("Length of post = %v, want %v", got, want)
    }
}
```

**最佳实践：** 如果 `postLength` 并非平凡，那么独立于任何使用它的测试直接测试它是有意义的。

参见：

*   [相等性比较与差异](#types-of-equality)
*   [打印差异](#print-diffs)
*   关于测试辅助函数与断言辅助函数之间区别的更多内容，参见[最佳实践](go-best-practices.md#test-functions)
*   [Go FAQ] 中关于[测试框架]及其刻意缺席的部分

[useful failure messages]: #useful-test-failures
[`fmt`]: https://golang.org/pkg/fmt/
[marking test helpers]: #mark-test-helpers
[Go FAQ]: https://go.dev/doc/faq
[testing frameworks]: https://go.dev/doc/faq#testing_framework

<a id="identify-the-function"></a>

### 指明函数

在大多数测试中，失败消息应当包含失败的函数名，即使从测试函数名看这似乎显而易见。具体来说，你的失败消息应当是 `YourFunc(%v) = %v, want %v`，而不是仅仅 `got %v, want %v`。

<a id="identify-the-input"></a>

### 指明输入

在大多数测试中，如果函数输入很短，失败消息应当包含它们。如果输入的相关属性不明显（例如，因为输入很大或不透明），你应当用对所测内容的描述来命名测试用例，并把该描述作为错误消息的一部分打印出来。

<a id="got-before-want"></a>

### got 在 want 之前

测试输出应当先包含函数返回的实际值，再打印期望的值。打印测试输出的标准格式是 `YourFunc(%v) = %v, want %v`。在你会写「actual」和「expected」的地方，优先分别使用「got」和「want」这两个词。

对于差异，方向性不那么明显，因此包含一个有助于解读失败的键很重要。参见[打印差异一节]。无论你在失败消息中使用哪种差异顺序，都应当把它明确地标明为失败消息的一部分，因为现有代码在顺序上并不一致。

[section on printing diffs]: #print-diffs

<a id="compare-full-structures"></a>

### 完整结构比较

如果你的函数返回一个结构体（或任何具有多个字段的数据类型，如切片、数组和映射），避免编写对结构体逐字段手工比较的测试代码。相反，构造你期望函数返回的数据，并使用[深度比较]直接比较。

**注意：** 如果你的数据包含会掩盖测试意图的无关字段，这不适用。

如果你的结构体需要按近似（或等价的语义）相等来比较，或者它包含无法按相等比较的字段（例如，其中一个字段是 `io.Reader`），那么用 [`cmpopts`] 选项（如 [`cmpopts.IgnoreInterfaces`]）来调整 [`cmp.Diff`] 或 [`cmp.Equal`] 比较，也许能满足你的需求（[示例](https://play.golang.org/p/vrCUNVfxsvF)）。

如果你的函数返回多个返回值，你不需要在比较之前把它们包装进结构体。只需逐个比较这些返回值并打印它们。

```go
// Good:
val, multi, tail, err := strconv.UnquoteChar(`\"Fran & Freddie's Diner\"`, '"')
if err != nil {
  t.Fatalf(...)
}
if val != `"` {
  t.Errorf(...)
}
if multi {
  t.Errorf(...)
}
if tail != `Fran & Freddie's Diner"` {
  t.Errorf(...)
}
```

[deep comparison]: #types-of-equality
[`cmpopts`]: https://pkg.go.dev/github.com/google/go-cmp/cmp/cmpopts
[`cmpopts.IgnoreInterfaces`]: https://pkg.go.dev/github.com/google/go-cmp/cmp/cmpopts#IgnoreInterfaces

<a id="compare-stable-results"></a>

### 比较稳定的结果

避免比较可能依赖于你并不拥有的某个包的输出稳定性的结果。相反，测试应当比较语义上相关且稳定、能抵抗依赖变化的信息。对于返回格式化字符串或序列化字节的功能，通常不能安全地假定其输出是稳定的。

例如，[`json.Marshal`] 可能改变（过去也确实改变过）它输出的具体字节。如果 `json` 包改变了它序列化字节的方式，对 JSON 字符串做字符串相等性检查的测试可能会失败。相反，更稳健的测试会解析 JSON 字符串的内容，并确保它与某个期望的数据结构在语义上等价。

[`json.Marshal`]: https://golang.org/pkg/encoding/json/#Marshal

<a id="keep-going"></a>

### 继续执行

测试应当尽可能长时间地继续执行，即使在失败之后也是如此，以便在一次运行中打印出所有失败的检查。这样，正在修复失败测试的开发者就不必在修复每个 bug 后重新运行测试来寻找下一个 bug。

报告不匹配时，优先调用 `t.Error` 而非 `t.Fatal`。当比较函数输出的若干不同属性时，对这些比较中的每一个都使用 `t.Error`。

```go
// Good:
gotMean, gotVariance, err := MyDistribution(input)
if err != nil {
  t.Fatalf("MyDistribution(%v) returned unexpected error: %v", input, err)
}
if diff := cmp.Diff(wantMean, gotMean); diff != "" {
  t.Errorf("MyDistribution(%v) returned unexpected difference in mean value (-want +got):\n%s", input, diff)
}
if diff := cmp.Diff(wantVariance, gotVariance); diff != "" {
  t.Errorf("MyDistribution(%v) returned unexpected difference in variance value (-want +got):\n%s", input, diff)
}
```

调用 `t.Fatal` 主要用于报告意外情况（如错误或输出不匹配），此时后续失败将毫无意义，甚至可能误导调查者。注意下面的代码先调用 `t.Fatalf`，*然后*调用 `t.Errorf`：

```go
// Good:
gotEncoded := Encode(input)
if gotEncoded != wantEncoded {
  t.Fatalf("Encode(%q) = %q, want %q", input, gotEncoded, wantEncoded)
  // It doesn't make sense to decode from unexpected encoded input.
}
gotDecoded, err := Decode(gotEncoded)
if err != nil {
  t.Fatalf("Decode(%q) returned unexpected error: %v", gotEncoded, err)
}
if gotDecoded != input {
  t.Errorf("Decode(%q) = %q, want %q", gotEncoded, gotDecoded, input)
}
```

对于表驱动测试，考虑使用子测试，并使用 `t.Fatal` 而非 `t.Error` 加 `continue`。另见 [GoTip #25：子测试：让你的测试更精简](go-style.md#gotip)。

**最佳实践：** 关于何时应当使用 `t.Fatal` 的更多讨论，参见[最佳实践](go-best-practices.md#t-fatal)。

<a id="types-of-equality"></a>

### 相等性比较与差异

`==` 运算符使用[语言定义的比较]来求相等性。标量值（数字、布尔值等）按其值比较，但只有某些结构体和接口能以这种方式比较。指针的比较基于它们是否指向同一个变量，而不是基于它们所指向的值是否相等。

[`cmp`] 包可以比较 `==` 无法恰当处理的更复杂的数据结构，例如切片。使用 [`cmp.Equal`] 做相等性比较，使用 [`cmp.Diff`] 获取对象之间人类可读的差异。

```go
// Good:
want := &Doc{
    Type:     "blogPost",
    Comments: 2,
    Body:     "This is the post body.",
    Authors:  []string{"isaac", "albert", "emmy"},
}
if !cmp.Equal(got, want) {
    t.Errorf("AddPost() = %+v, want %+v", got, want)
}
```

作为一个通用的比较库，`cmp` 可能不知道如何比较某些类型。例如，只有传入 [`protocmp.Transform`] 选项，它才能比较 protocol buffer 消息。

<!-- 这里 want 和 got 的顺序是刻意的。参见 #print-diffs 中的注释。 -->

```go
// Good:
if diff := cmp.Diff(want, got, protocmp.Transform()); diff != "" {
    t.Errorf("Foo() returned unexpected difference in protobuf messages (-want +got):\n%s", diff)
}
```

尽管 `cmp` 包不属于 Go 标准库，但它由 Go 团队维护，并且应当随时间产生稳定的相等性结果。它是用户可配置的，应当能满足大多数比较需求。

[language-defined comparisons]: http://golang.org/ref/spec#Comparison_operators
[`cmp`]: https://pkg.go.dev/github.com/google/go-cmp/cmp
[`cmp.Equal`]: https://pkg.go.dev/github.com/google/go-cmp/cmp#Equal
[`cmp.Diff`]: https://pkg.go.dev/github.com/google/go-cmp/cmp#Diff
[`protocmp.Transform`]: https://pkg.go.dev/google.golang.org/protobuf/testing/protocmp#Transform

现有代码可能会使用以下较旧的库，并且可以继续使用它们以保持一致：

*   [`pretty`] 生成美观的差异报告。然而，它相当刻意地把具有相同视觉表示的值视为相等。特别是，`pretty` 不会捕捉 nil 切片与空切片之间的差异，对不同接口实现但字段相同的情况不敏感，并且可以用嵌套映射作为与结构体值比较的基础。它还会在生成差异之前把整个值序列化成字符串，因此不是比较大值的好选择。默认情况下，它比较未导出字段，这使它对你依赖项中实现细节的变化很敏感。出于这个原因，不适合对 protobuf 消息使用 `pretty`。

[`pretty`]: https://pkg.go.dev/github.com/kylelemons/godebug/pretty

新代码优先使用 `cmp`，并且在切实可行的情况下，值得考虑把较旧的代码更新为使用 `cmp`。

较旧的代码可能会使用标准库的 `reflect.DeepEqual` 函数来比较复杂结构体。不应当用 `reflect.DeepEqual` 检查相等性，因为它对未导出字段及其他实现细节的变化很敏感。使用 `reflect.DeepEqual` 的代码应当更新为上述库之一。

**注意：** `cmp` 包是为测试而非生产使用而设计的。因此，当它怀疑比较执行不当时，可能会 panic，以指导用户如何改进测试、使其不那么脆弱。鉴于 cmp 倾向于 panic，它不适合用于生产代码，因为一次虚假的 panic 可能是致命的。

<a id="level-of-detail"></a>

### 详细程度

适合大多数 Go 测试的常规失败消息是 `YourFunc(%v) = %v, want %v`。然而，有些情况可能需要更多或更少的细节：

*   执行复杂交互的测试也应当描述这些交互。例如，如果同一个 `YourFunc` 被调用了多次，要指明是哪次调用导致测试失败。如果需要知道系统的任何额外状态，就把它包含在失败输出中（或至少包含在日志中）。
*   如果数据是一个带有大量样板内容的复杂结构体，可以只在消息中描述重要的部分，但不要把数据过度遮蔽。
*   设置失败不需要同样的详细程度。如果某个测试辅助函数填充 Spanner 表但 Spanner 宕机了，你大概不需要包含你原本打算存入数据库的是哪个测试输入。`t.Fatalf("Setup: Failed to set up test database: %s", err)` 通常就足以帮助解决问题了。

**提示：** 让你的失败模式在开发过程中触发。审视失败消息长什么样，以及维护者能否有效地处理这个失败。

有一些能清晰地重现测试输入和输出的技巧：

*   打印字符串数据时，[`%q` 常常很有用](#use-percent-q)，可以强调该值很重要，并更容易发现坏值。
*   打印（小型）结构体时，`%+v` 可能比 `%v` 更有用。
*   较大的值校验失败时，[打印差异](#print-diffs)可以让人更容易理解失败的原因。

<a id="print-diffs"></a>

### 打印差异

如果你的函数返回很大的输出，那么测试失败时，阅读失败消息的人可能很难找到差异。不要同时打印返回值和要求的值，而是做一个差异。

为这样的值计算差异时，首选 `cmp.Diff`，对于新测试和新代码尤其如此，但也可以使用其他工具。关于每个函数的优势和弱点，参见[相等性的类型]。

*   [`cmp.Diff`]

*   [`pretty.Compare`]

你可以使用 [`diff`] 包来比较多行字符串或字符串列表。你可以把它用作其他类型差异的构建块。

[types of equality]: #types-of-equality
[`diff`]: https://pkg.go.dev/github.com/kylelemons/godebug/diff
[`pretty.Compare`]: https://pkg.go.dev/github.com/kylelemons/godebug/pretty#Compare

在你的失败消息中添加一些文字，说明差异的方向。

<!--
这些示例中 want 和 got 的反转顺序是刻意的，因为这是整个 Google 代码库中通行的顺序。不对使用哪种顺序表态同样是刻意的，因为对于哪种「最易读」并没有共识。

-->

*   当你使用 `cmp`、`pretty` 和 `diff` 包时（如果你把 `(want, got)` 传给函数），类似 `diff (-want +got)` 这样的写法就很好，因为你在格式字符串中添加的 `-` 和 `+` 会与差异行开头实际出现的 `-` 和 `+` 相匹配。如果你把 `(got, want)` 传给函数，正确的键就应当是 `(-got +want)`。

*   `messagediff` 包使用不同的输出格式，所以当你使用它时（如果你把 `(want, got)` 传给函数），`diff (want -> got)` 这样的消息是合适的，因为箭头的方向会与「modified」行中箭头的方向相匹配。

差异会跨越多行，所以你应当在打印差异之前先打印一个换行。

<a id="test-error-semantics"></a>

### 测试错误语义

当单元测试执行字符串比较，或使用原版 `cmp` 来检查对于特定输入是否返回特定类型的错误时，你可能会发现，如果将来这些错误消息中的任何一条被改写，你的测试就会变得脆弱。由于这有可能把你的单元测试变成一个变更探测器（见 [TotT: Change-Detector Tests Considered Harmful][tott-350]），不要用字符串比较来检查你的函数返回哪种类型的错误。不过，允许用字符串比较来检查来自被测包的错误消息是否满足某些属性，例如，它是否包含参数名。

Go 中的错误值通常有一个供人阅读的部分和一个用于语义控制流的部分。测试应当只测可靠可观测的语义信息，而不是供人调试的展示信息，因为后者常常会在将来发生变化。关于构造具有语义含义的错误的指导，参见[关于错误的最佳实践](go-best-practices.md#error-handling)。如果来自你无法控制的依赖项的错误缺乏足够的语义信息，考虑向所有者提交一个 bug 以帮助改进 API，而不是依赖解析错误消息。

在单元测试中，通常只关心是否发生了错误。如果是这样，那么当你期望有错误时，只测试错误是否非 nil 就足够了。如果你想测试该错误在语义上与某个其他错误相匹配，那么考虑使用 [`errors.Is`] 或带 [`cmpopts.EquateErrors`] 的 `cmp`。

> **注意：** 如果测试使用 [`cmpopts.EquateErrors`]，但它的所有 `wantErr` 值要么是 `nil`，要么是 `cmpopts.AnyError`，那么使用 `cmp` 就是[不必要的机制](go-style.md#least-mechanism)。通过把 want 字段改成 `bool` 来简化代码。然后你就可以用 `!=` 做简单的比较。
>
> ```go
> // Good:
> err := f(test.input)
> if gotErr := err != nil; gotErr != test.wantErr {
>     t.Errorf("f(%q) = %v, want error presence = %v", test.input, err, test.wantErr)
> }
> ```

另见 [GoTip #13：为检查而设计错误](go-style.md#gotip)。

[tott-350]: https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html
[`cmpopts.EquateErrors`]: https://pkg.go.dev/github.com/google/go-cmp/cmp/cmpopts#EquateErrors
[`errors.Is`]: https://pkg.go.dev/errors#Is

<a id="test-structure"></a>

## 测试结构

<a id="subtests"></a>

### 子测试

Go 标准测试库提供了[定义子测试]的能力。这在准备与清理、控制并行度以及测试过滤上带来了灵活性。子测试可能很有用（尤其是对表驱动测试而言），但使用它们并非强制要求。另见[关于子测试的 Go 博客文章](https://blog.golang.org/subtests)。

子测试不应当依赖其他用例的执行来获得成功或初始状态，因为子测试应当能够用 `go test -run` 标志或 Bazel [测试过滤器]表达式单独运行。

[define subtests]: https://pkg.go.dev/testing#hdr-Subtests_and_Sub_benchmarks
[test filter]: https://bazel.build/docs/user-manual#test-filter

<a id="subtest-names"></a>

#### 子测试命名

给子测试命名时，要使其在测试输出中可读，并且对使用测试过滤的命令行用户有用。当你用 `t.Run` 创建子测试时，第一个参数会被用作该测试的描述性名称。为确保测试结果对阅读日志的人来说清晰可辨，请选择在转义之后仍然有用、可读的子测试名。要把子测试名更多地看作函数标识符，而不是散文式的描述。

测试运行器会把空格替换成下划线，并转义不可打印字符。为确保测试日志与源码之间能准确对应，建议避免在子测试名中使用这些字符。

如果你的测试数据得益于更长的描述，不妨把该描述放到单独的字段里（也许用 `t.Log` 打印，或与失败消息放在一起）。

子测试可以用 [Go 测试运行器]的标志或 Bazel [测试过滤器]单独运行，所以要选择既具描述性、又易于键入的名字。

> **警告：**斜杠字符在子测试名中尤其不友好，因为它们对测试过滤器有[特殊含义]。
>
> > ```sh
> > # Bad:
> > # Assuming TestTime and t.Run("America/New_York", ...)
> > bazel test :mytest --test_filter="Time/New_York"    # Runs nothing!
> > bazel test :mytest --test_filter="Time//New_York"   # Correct, but awkward.
> > ```

为[标识函数输入]，应把输入放进测试的失败消息里，这样它们就不会被测试运行器转义。

```go
// Good:
func TestTranslate(t *testing.T) {
    data := []struct {
        name, desc, srcLang, dstLang, srcText, wantDstText string
    }{
        {
            name:        "hu=en_bug-1234",
            desc:        "regression test following bug 1234. contact: cleese",
            srcLang:     "hu",
            srcText:     "cigarettát és egy öngyújtót kérek",
            dstLang:     "en",
            wantDstText: "cigarettes and a lighter please",
        }, // ...
    }
    for _, d := range data {
        t.Run(d.name, func(t *testing.T) {
            got := Translate(d.srcLang, d.dstLang, d.srcText)
            if got != d.wantDstText {
                t.Errorf("%s\nTranslate(%q, %q, %q) = %q, want %q",
                    d.desc, d.srcLang, d.dstLang, d.srcText, got, d.wantDstText)
            }
        })
    }
}
```

以下是几个应当避免的例子：

```go
// Bad:
// Too wordy.
t.Run("check that there is no mention of scratched records or hovercrafts", ...)
// Slashes cause problems on the command line.
t.Run("AM/PM confusion", ...)
```

另见 [Go Tip #117：子测试命名](go-style.md#gotip)。

[Go test runner]: https://golang.org/cmd/go/#hdr-Testing_flags
[identify the inputs]: #identify-the-input
[special meaning for test filters]: https://blog.golang.org/subtests#:~:text=Perhaps%20a%20bit,match%20any%20tests

<a id="table-driven-tests"></a>

### 表驱动测试

当许多不同的测试用例可以用相似的测试逻辑来测试时，就使用表驱动测试。

*   在测试某个函数的实际输出是否等于期望输出时。例如 [`fmt.Sprintf` 的众多测试]，或下面的最小片段。
*   在测试某个函数的输出是否始终符合同一组不变式时。例如 [`net.Dial` 的测试]。

[tests of `fmt.Sprintf`]: https://cs.opensource.google/go/go/+/master:src/fmt/fmt_test.go
[tests for `net.Dial`]: https://cs.opensource.google/go/go/+/master:src/net/dial_test.go;l=318;drc=5b606a9d2b7649532fe25794fa6b99bd24e7697c

下面是表驱动测试的最小结构。如有需要，你可以使用不同的名字，或者添加额外的设施，比如子测试、准备与清理函数。始终要记住[有用的测试失败](#useful-test-failures)。

```go
// Good:
func TestCompare(t *testing.T) {
    compareTests := []struct {
        a, b string
        want int
    }{
        {"", "", 0},
        {"a", "", 1},
        {"", "a", -1},
        {"abc", "abc", 0},
        {"ab", "abc", -1},
        {"abc", "ab", 1},
        {"x", "ab", 1},
        {"ab", "x", -1},
        {"x", "a", 1},
        {"b", "x", -1},
        // test runtime·memeq's chunked implementation
        {"abcdefgh", "abcdefgh", 0},
        {"abcdefghi", "abcdefghi", 0},
        {"abcdefghi", "abcdefghj", -1},
    }

    for _, test := range compareTests {
        got := Compare(test.a, test.b)
        if got != test.want {
            t.Errorf("Compare(%q, %q) = %v, want %v", test.a, test.b, got, test.want)
        }
    }
}
```

**注意**：上面这个示例里的失败消息满足了[标识函数](#identify-the-function)与[标识输入](#identify-the-input)的指引。无需[用数字标识行](#table-tests-identifying-the-row)。

当某些测试用例需要用与其他用例不同的逻辑来检查时，适合写多个测试函数，如 [GoTip #50：不相交的表测试]中所述。

当额外的测试用例很简单（例如基本的错误检查），并且不会在表测试的循环体里引入带条件的代码流程时，可以把该用例放进现有测试里，不过要小心使用这样的逻辑。今天看来简单的东西，可能会自然地滋长成难以维护的东西。

例如：

```go
func TestDivide(t *testing.T) {
    tests := []struct {
        dividend, divisor int
        want              int
        wantErr           bool
    }{
        {
            dividend: 4,
            divisor:  2,
            want:     2,
        },
        {
            dividend: 10,
            divisor:  2,
            want:     5,
        },
        {
            dividend: 1,
            divisor:  0,
            wantErr:  true,
        },
    }

    for _, test := range tests {
        got, err := Divide(test.dividend, test.divisor)
        if (err != nil) != test.wantErr {
            t.Errorf("Divide(%d, %d) error = %v, want error presence = %t", test.dividend, test.divisor, err, test.wantErr)
        }

        // In this example, we're only testing the value result when the tested function didn't fail.
        if err != nil {
            continue
        }

        if got != test.want {
            t.Errorf("Divide(%d, %d) = %d, want %d", test.dividend, test.divisor, got, test.want)
        }
    }
}
```

当表中的每个条目都基于输入拥有专门逻辑时，测试代码里更复杂的逻辑——比如基于测试准备中的条件差异（通常基于表测试的输入参数）而做的复杂错误检查——可能会[难以理解](go-style.md#maintainability)。如果测试用例逻辑不同但准备过程相同，那么在单个测试函数里用一串[子测试](#subtests)可能更可读。为了保持测试主体的可读性，测试辅助函数也可能有助于简化测试准备。

你可以把表驱动测试与多个测试函数结合起来。例如，在既要测试某个函数的输出与期望输出完全一致，又要测试该函数对无效输入返回非 nil 错误时，最好的做法是写两个独立的表驱动测试函数：一个用于正常的非错误输出，一个用于错误输出。

[GoTip #50: Disjoint Table Tests]: go-style.md#gotip

<a id="table-tests-data-driven"></a>

#### 数据驱动的测试用例

表测试的行有时会变得复杂，行里的取值决定了测试用例内部的条件行为。为了可读性，测试用例之间重复所带来的额外清晰是必要的。

```go
// Good:
type decodeCase struct {
    name   string
    input  string
    output string
    err    error
}

func TestDecode(t *testing.T) {
    // setupCodex is slow as it creates a real Codex for the test.
    codex := setupCodex(t)

    var tests []decodeCase // rows omitted for brevity

    for _, test := range tests {
        t.Run(test.name, func(t *testing.T) {
            output, err := Decode(test.input, codex)
            if got, want := output, test.output; got != want {
                t.Errorf("Decode(%q) = %v, want %v", test.input, got, want)
            }
            if got, want := err, test.err; !cmp.Equal(got, want) {
                t.Errorf("Decode(%q) err %q, want %q", test.input, got, want)
            }
        })
    }
}

func TestDecodeWithFake(t *testing.T) {
    // A fakeCodex is a fast approximation of a real Codex.
    codex := newFakeCodex()

    var tests []decodeCase // rows omitted for brevity

    for _, test := range tests {
        t.Run(test.name, func(t *testing.T) {
            output, err := Decode(test.input, codex)
            if got, want := output, test.output; got != want {
                t.Errorf("Decode(%q) = %v, want %v", test.input, got, want)
            }
            if got, want := err, test.err; !cmp.Equal(got, want) {
                t.Errorf("Decode(%q) err %q, want %q", test.input, got, want)
            }
        })
    }
}
```

在下面的反例中，注意在用例准备里有多难区分每个测试用例使用的是哪种 `Codex`。（高亮部分与 [TotT：数据驱动的陷阱！][tott-97]的建议相冲突。）

```go
// Bad:
type decodeCase struct {
  name   string
  input  string
  codex  testCodex
  output string
  err    error
}

type testCodex int

const (
  fake testCodex = iota
  prod
)

func TestDecode(t *testing.T) {
  var tests []decodeCase // rows omitted for brevity

  for _, test := tests {
    t.Run(test.name, func(t *testing.T) {
      var codex Codex
      switch test.codex {
      case fake:
        codex = newFakeCodex()
      case prod:
        codex = setupCodex(t)
      default:
        t.Fatalf("Unknown codex type: %v", codex)
      }
      output, err := Decode(test.input, codex)
      if got, want := output, test.output; got != want {
        t.Errorf("Decode(%q) = %q, want %q", test.input, got, want)
      }
      if got, want := err, test.err; !cmp.Equal(got, want) {
        t.Errorf("Decode(%q) err %q, want %q", test.input, got, want)
      }
    })
  }
}
```

[tott-97]: https://testing.googleblog.com/2008/09/tott-data-driven-traps.html

<a id="table-tests-identifying-the-row"></a>

#### 标识行

不要用测试在测试表中的下标来替代给测试命名或打印输入。没人愿意翻遍你的测试表去数条目，好弄清楚到底哪个测试用例失败了。

```go
// Bad:
tests := []struct {
    input, want string
}{
    {"hello", "HELLO"},
    {"wORld", "WORLD"},
}
for i, d := range tests {
    if strings.ToUpper(d.input) != d.want {
        t.Errorf("Failed on case #%d", i)
    }
}
```

给测试结构体加上测试描述，并随失败消息一起打印。使用子测试时，你的子测试名应当能有效地标识行。

**重要：**尽管 `t.Run` 限定了输出与执行的作用域，你仍必须始终[标识输入]。表测试的行名必须遵循[子测试命名]的指引。

[identify the input]: #identify-the-input
[subtest naming]: #subtest-names

<a id="mark-test-helpers"></a>

### 测试辅助函数

测试辅助函数是执行准备或清理任务的函数。测试辅助函数中出现的所有失败都应当是环境的失败（而不是被测代码的失败）——例如，当本机上没有更多空闲端口、导致测试数据库无法启动时。

如果你传入 `*testing.T`，请调用 [`t.Helper`]，以便把测试辅助函数里的失败归到调用该辅助函数的那一行。这个参数应当放在 [context](#contexts) 参数之后（如果有的话），并放在其余任何参数之前。

```go
// Good:
func TestSomeFunction(t *testing.T) {
    golden := readFile(t, "testdata/golden-result.txt")
    // ... tests against golden ...
}

// readFile returns the contents of a data file.
// It must only be called from the same goroutine as started the test.
func readFile(t *testing.T, filename string) string {
    t.Helper()
    contents, err := runfiles.ReadFile(filename)
    if err != nil {
        t.Fatal(err)
    }
    return string(contents)
}
```

当这种模式会掩盖测试失败与其成因条件之间的联系时，不要使用它。具体来说，关于[断言库](#assert)的指引仍然适用，并且不应当用 [`t.Helper`] 来实现这类库。

**提示：**关于测试辅助函数与断言辅助函数之间区别的更多内容，见[最佳实践](go-best-practices.md#test-functions)。

尽管上面提到的是 `*testing.T`，但这些建议中的大部分对基准测试与模糊测试的辅助函数同样适用。

[`t.Helper`]: https://pkg.go.dev/testing#T.Helper

<a id="test-package"></a>

### 测试包

<a id="TOC-TestPackage"></a>

<a id="test-same-package"></a>

#### 同一包中的测试

测试可以定义在与被测代码相同的包里。

要在同一包中写测试：

*   把测试放在 `foo_test.go` 文件里
*   测试文件用 `package foo`
*   不要显式导入被测的包

```build
# Good:
go_library(
    name = "foo",
    srcs = ["foo.go"],
    deps = [
        ...
    ],
)

go_test(
    name = "foo_test",
    size = "small",
    srcs = ["foo_test.go"],
    library = ":foo",
    deps = [
        ...
    ],
)
```

同一包中的测试可以访问该包中未导出的标识符。这可以实现更好的测试覆盖率以及更简洁的测试。请注意，测试中声明的任何[示例]都不会带有用户在自己代码中所需的包名。

[`library`]: https://github.com/bazelbuild/rules_go/blob/master/docs/go/core/rules.md#go_library
[examples]: #examples

<a id="test-different-package"></a>

#### 不同包中的测试

把测试定义在与被测代码相同的包里并不总是合适，甚至并不总是可能。在这些情况下，使用带 `_test` 后缀的包名。这是对[包名](#package-names)「不用下划线」规则的一个例外。例如：

*   如果某个集成测试没有明显归属的库

    ```go
    // Good:
    package gmailintegration_test

    import "testing"
    ```

*   如果在同一包中定义测试会导致循环依赖

    ```go
    // Good:
    package fireworks_test

    import (
      "fireworks"
      "fireworkstestutil" // fireworkstestutil also imports fireworks
    )
    ```

<a id="use-package-testing"></a>

### 使用 `testing` 包

Go 标准库提供了 [`testing` 包]。这是在 Google 代码库中允许 Go 代码使用的唯一测试框架。特别是，不允许使用[断言库](#assert)和第三方测试框架。

`testing` 包为编写良好的测试提供了一套最小而完整的功能：

*   顶层测试
*   基准测试
*   [可运行的示例](https://blog.golang.org/examples)
*   子测试
*   日志
*   失败与致命失败

这些功能被设计为与[复合字面量]和[带初始化语句的 if]语法等核心语言特性协同工作，使测试作者能够编写[清晰、可读且可维护的测试]。

[`testing` package]: https://pkg.go.dev/testing
[composite literal]: https://go.dev/ref/spec#Composite_literals
[if-with-initializer]: https://go.dev/ref/spec#If_statements

<a id="non-decisions"></a>

## 非决策

风格指南不可能为所有事项列举出正面的规定，也不可能列举出它不发表意见的所有事项。话虽如此，以下是少数几件可读性社区先前争论过、但未达成共识的事情。

*   **用零值初始化局部变量**。`var i int` 与 `i := 0` 等价。另见[初始化最佳实践]。
*   **空的复合字面量 vs. `new` 或 `make`**。`&File{}` 与 `new(File)` 等价。`map[string]bool{}` 与 `make(map[string]bool)` 也一样。另见[复合声明最佳实践]。
*   **cmp.Diff 调用中 got、want 参数的顺序**。在局部保持一致，并在失败消息里[加入图例](#print-diffs)。
*   **非格式化字符串上的 `errors.New` vs `fmt.Errorf`**。`errors.New("foo")` 与 `fmt.Errorf("foo")` 可以互换使用。

如果出现再次提及它们的特殊情况，可读性导师可能会提出一条可选的评论，但一般而言，作者可以自由地在给定情形下选择自己喜欢的风格。

当然，如果风格指南未涵盖的某些内容确实需要更多讨论，欢迎作者提问——无论是在具体的评审中，还是在内部留言板上。

[composite declaration best practices]: go-best-practices.md#vardeclcomposite
[initialization best practices]: go-best-practices.md#vardeclinitialization
