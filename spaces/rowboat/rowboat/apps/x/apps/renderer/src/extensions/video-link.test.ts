// VibeMind addition (Flissel/rowboat vibemind): tests for the video link node (save and load halves).
import { describe, expect, it, vi } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Link from '@tiptap/extension-link'
import TaskList from '@tiptap/extension-task-list'
import TaskItem from '@tiptap/extension-task-item'
import { Markdown } from 'tiptap-markdown'

// markdown-editor pulls in embed-block, whose react-tweet import loads a .css file Node cannot parse.
// Same stub as src/components/spaces/checkbox-toggle.test.tsx.
vi.mock('react-tweet', () => ({ Tweet: () => null }))

import { blockToMarkdown } from '@/components/markdown-editor'
import { VideoLinkExtension } from './video-link'

describe('video links', () => {
  it('is a block node named videoLink', () => {
    expect(VideoLinkExtension.name).toBe('videoLink')
  })

  it('survives saving as a markdown link', () => {
    expect(blockToMarkdown({ type: 'videoLink', attrs: { src: 'http://localhost:8977/demo.mp4', title: 'Demo' } }))
      .toBe('[Demo](http://localhost:8977/demo.mp4)')
  })

  it('gets a default title when it has none', () => {
    expect(blockToMarkdown({ type: 'videoLink', attrs: { src: 'http://x/a.webm', title: '' } }))
      .toBe('[▶ Video abspielen](http://x/a.webm)')
  })
})

type DocNode = { type?: string; attrs?: Record<string, unknown>; content?: DocNode[]; text?: string }

function loadMarkdown(markdown: string): DocNode[] {
  const editor = new Editor({
    element: document.createElement('div'),
    extensions: [
      StarterKit.configure({ link: false }),
      Link.configure({ openOnClick: false }),
      TaskList,
      TaskItem.configure({ nested: true }),
      VideoLinkExtension,
      Markdown.configure({ html: true, breaks: true }),
    ],
  })
  // The editor loads a note through setContent (a transaction), which is what triggers the transform.
  editor.chain().setMeta('addToHistory', false).setContent(markdown).run()
  const all = (editor.getJSON().content ?? []) as DocNode[]
  // StarterKit's trailing-node rule appends an empty paragraph after a final block node; that is not content.
  const last = all[all.length - 1]
  const blocks = all.length > 1 && last.type === 'paragraph' && !last.content?.length ? all.slice(0, -1) : all
  editor.destroy()
  return blocks
}

describe('video link loading', () => {
  it('turns a paragraph with a single video link into a videoLink node', () => {
    const blocks = loadMarkdown('[Demo](http://x/a.mp4)')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('videoLink')
    expect(blocks[0].attrs).toMatchObject({ src: 'http://x/a.mp4', title: 'Demo' })
  })

  it('keeps every video link and the paragraph between them intact', () => {
    const blocks = loadMarkdown('[One](http://x/a.mp4)\n\nJust some text\n\n[Two](http://x/b.webm)\n\n[Three](http://x/c.mov)')
    expect(blocks.map(b => b.type)).toEqual(['videoLink', 'paragraph', 'videoLink', 'videoLink'])
    expect(blocks[0].attrs).toMatchObject({ src: 'http://x/a.mp4', title: 'One' })
    expect(blocks[1].content?.[0].text).toBe('Just some text')
    expect(blocks[2].attrs).toMatchObject({ src: 'http://x/b.webm', title: 'Two' })
    expect(blocks[3].attrs).toMatchObject({ src: 'http://x/c.mov', title: 'Three' })
  })

  it('leaves links to non-video targets alone', () => {
    const blocks = loadMarkdown('[Site](http://x/index.html)')
    expect(blocks.map(b => b.type)).toEqual(['paragraph'])
  })
})

function saveMarkdown(markdown: string): string {
  return loadMarkdown(markdown).map(b => blockToMarkdown(b)).join('\n\n')
}

describe('video links nested in other blocks', () => {
  it('keeps video links in a bullet list when the note is saved', () => {
    const saved = saveMarkdown('- [Intro](http://x/a.mp4)\n- [Demo](http://x/b.mp4)')
    expect(saved).toContain('[Intro](http://x/a.mp4)')
    expect(saved).toContain('[Demo](http://x/b.mp4)')
  })

  it('keeps video links in a task list when the note is saved', () => {
    const saved = saveMarkdown('- [ ] [Intro](http://x/a.mp4)\n- [x] [Demo](http://x/b.mp4)')
    expect(saved).toContain('[Intro](http://x/a.mp4)')
    expect(saved).toContain('[Demo](http://x/b.mp4)')
  })

  it('keeps a video link in a blockquote when the note is saved', () => {
    expect(saveMarkdown('> [Intro](http://x/a.mp4)')).toContain('[Intro](http://x/a.mp4)')
  })

  it('does not turn nested video links into player nodes', () => {
    const blocks = loadMarkdown('- [Intro](http://x/a.mp4)')
    expect(JSON.stringify(blocks)).not.toContain('videoLink')
  })
})
