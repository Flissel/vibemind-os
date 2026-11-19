// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17 fix
// round 2 (review m-3). In the embedded view an answer the caller may not give
// (a human-only Allow) is refused by the host; the prompt card says where to
// answer instead of failing silently.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PermissionRequest } from './permission-request'
import { isRestrictedAnswerRefusal } from '@/lib/restricted-answer'

afterEach(cleanup)

const toolCall = { type: 'tool-call' as const, toolCallId: 't1', toolName: 'addMcpServer', arguments: { serverName: 's' } }

it('shows an inline hint when the host refuses the answer for this caller', async () => {
  const onApprove = vi.fn(async () => {
    throw new Error("Error invoking remote method 'sessions:respondToPermission': Error: addMcpServer needs the human's own answer; this caller may only deny it")
  })
  render(<PermissionRequest toolCall={toolCall} onApprove={onApprove} onDeny={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /allow/i }))
  await waitFor(() => expect(screen.getByText(/answer this in the Rowboat window/i)).toBeTruthy())
})

it('shows no hint when the answer is accepted', async () => {
  render(<PermissionRequest toolCall={toolCall} onApprove={vi.fn(async () => {})} onDeny={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /allow/i }))
  await new Promise((r) => setTimeout(r, 10))
  expect(screen.queryByText(/answer this in the Rowboat window/i)).toBeNull()
})

it('recognizes the refusals of every host and runtime path, and nothing else', () => {
  for (const message of [
    "x needs the human's own answer; this caller may only deny it",
    'this caller may not answer with scope "always"',
    'This caller may not read or change protected workspace entries (config/, keys, channels/, app data).',
    "allow_always needs the human's own answer; this caller may only allow once or reject.",
  ]) {
    expect(isRestrictedAnswerRefusal(new Error(message)), message).toBe(true)
  }
  expect(isRestrictedAnswerRefusal(new Error('network down'))).toBe(false)
  expect(isRestrictedAnswerRefusal('needs the human')).toBe(false)
})
