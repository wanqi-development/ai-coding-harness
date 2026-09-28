import type { TodoItem } from './api'

/**
 * 待办树在界面上要不要画。
 *
 * 跑的时候它是进度条（用户就靠它看走到哪儿了），跑完就是噪音 —— 整棵树（含子项）
 * 全部完成、且这一轮已经收尾时不再展示；只要还有一项没做完就照旧显示，那是
 * 「哪儿没做完」的唯一线索。
 *
 * 为什么还要「这一轮已收尾」这个条件：只看「全绿」的话，模型中途把全部标成 completed
 * 的那一瞬间列表会闪一下消失，随后又加一项再冒出来。收尾时间（`Message.at`）在流结束后
 * 才落上，历史消息一律带着它，所以实时与历史是同一套判定。
 */
function itemDone(item: TodoItem): boolean {
  return item.status === 'completed' && (item.children ?? []).every(itemDone)
}

/** 整棵树（含子项）是不是全完成了。空树不算完成 —— 没清单就别画。 */
export function todosAllDone(items: TodoItem[]): boolean {
  return items.length > 0 && items.every(itemDone)
}

/**
 * 这棵树要不要显示。
 *
 * `finished` = 这一轮已经收尾（消息有了收尾时间）。
 */
export function shouldShowTodos(items: TodoItem[], finished: boolean): boolean {
  if (!items.length) return false
  return !finished || !todosAllDone(items)
}
