import assert from 'node:assert/strict'
import test from 'node:test'

import { draftFor, withDraft } from '../src/lib/sessionDrafts.ts'

test('session drafts stay isolated when switching between sessions', () => {
  let drafts = withDraft({}, 'session-a', 'draft A')
  drafts = withDraft(drafts, 'session-b', 'draft B')

  assert.equal(draftFor(drafts, 'session-a'), 'draft A')
  assert.equal(draftFor(drafts, 'session-b'), 'draft B')
  assert.equal(draftFor(drafts, 'session-c'), '')
})

test('clearing one draft does not alter another session', () => {
  let drafts = withDraft({}, 'session-a', 'draft A')
  drafts = withDraft(drafts, 'session-b', 'draft B')
  drafts = withDraft(drafts, 'session-a', '')

  assert.equal(draftFor(drafts, 'session-a'), '')
  assert.equal(draftFor(drafts, 'session-b'), 'draft B')
})

test('writing outside a selected session is ignored', () => {
  const drafts = withDraft({}, '', 'orphan')

  assert.deepEqual(drafts, {})
})
