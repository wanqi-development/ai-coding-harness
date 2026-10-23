import type {
  ClarificationAnswer,
  ClarificationInfo,
  ClarificationStep,
} from './api'

/** 新旧协议归一：旧记录只有一个 question/options。 */
export function clarificationSteps(info: ClarificationInfo): ClarificationStep[] {
  if (info.steps?.length) return info.steps
  if (info.question && info.options?.length) {
    return [{ id: 'intent', question: info.question, options: info.options }]
  }
  return []
}

/** 多步答案 → 面向用户的短摘要。 */
export function clarificationAnswerLabels(
  info: ClarificationInfo,
  answers: ClarificationAnswer[],
): string[] {
  const steps = clarificationSteps(info)
  return answers.flatMap((answer) => {
    const step = steps.find((item) => item.id === answer.step_id)
    const option = step?.options[answer.choice]
    return option?.label ? [option.label] : []
  })
}
