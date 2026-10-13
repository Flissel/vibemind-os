// VibeMind addition (Flissel/rowboat vibemind): video links as inline players (vibemind-os f6695d46).
// Fix (Flissel/rowboat vibemind): replacement positions are mapped through the transaction, so several video links in one note all load.
// Fix (Flissel/rowboat vibemind): only top-level paragraphs become players, so video links in lists, quotes and tables survive saving.
/**
 * Video Link Extension for TipTap
 *
 * Detects markdown links pointing to .mp4/.webm/.mov files and renders
 * them as inline <video> players instead of clickable text links.
 *
 * Works with the VibeMind media server (localhost:8977) and any other
 * video URL.
 */

import { Node, mergeAttributes } from '@tiptap/react'
import { ReactNodeViewRenderer, NodeViewWrapper } from '@tiptap/react'
import { Plugin, PluginKey } from '@tiptap/pm/state'

const VIDEO_EXTENSIONS = /\.(mp4|webm|mov|avi)(\?.*)?$/i

function VideoPlayerView({ node }: { node: { attrs: Record<string, unknown> } }) {
  const src = (node.attrs.src as string) || ''
  const title = (node.attrs.title as string) || ''

  return (
    <NodeViewWrapper className="video-link-wrapper" data-type="video-link" contentEditable={false}>
      <div style={{
        borderRadius: '8px',
        overflow: 'hidden',
        background: '#111',
        margin: '8px 0',
        maxWidth: '100%',
      }}>
        <video
          src={src}
          style={{ width: '100%', display: 'block', maxHeight: '400px' }}
          controls
          preload="metadata"
        />
        {title && (
          <div style={{
            padding: '6px 10px',
            fontSize: '12px',
            color: '#999',
            background: '#1a1a1a',
          }}>
            {title}
          </div>
        )}
      </div>
    </NodeViewWrapper>
  )
}

export const VideoLinkExtension = Node.create({
  name: 'videoLink',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes() {
    return {
      src: { default: '' },
      title: { default: '' },
    }
  },

  parseHTML() {
    return [
      {
        tag: 'div[data-type="video-link"]',
        getAttrs(element) {
          const video = element.querySelector('video')
          return {
            src: video?.getAttribute('src') || '',
            title: element.getAttribute('data-title') || '',
          }
        },
      },
    ]
  },

  renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-type': 'video-link' })]
  },

  addNodeView() {
    return ReactNodeViewRenderer(VideoPlayerView)
  },

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey('videoLinkTransform'),
        appendTransaction: (_transactions, _oldState, newState) => {
          let tr = newState.tr
          let modified = false

          // Top-level paragraphs only: the editor's nested serializers (lists, blockquotes, tables) drop atom nodes.
          newState.doc.forEach((node, pos) => {
            // Find paragraph nodes that contain a single link to a video file
            if (node.type.name !== 'paragraph') return
            if (node.childCount !== 1) return

            const child = node.firstChild
            if (!child || !child.isText) return

            const linkMark = child.marks.find(m => m.type.name === 'link')
            if (!linkMark) return

            const href = linkMark.attrs.href as string
            if (!VIDEO_EXTENSIONS.test(href)) return

            // Replace the paragraph+link with a videoLink node
            const videoNode = newState.schema.nodes.videoLink?.create({
              src: href,
              title: child.text || '',
            })
            if (videoNode) {
              // forEach() reports positions in newState.doc; earlier replacements already shifted tr.doc.
              const from = tr.mapping.map(pos)
              const to = tr.mapping.map(pos + node.nodeSize)
              tr = tr.replaceWith(from, to, videoNode)
              modified = true
            }
          })

          return modified ? tr : null
        },
      }),
    ]
  },

  addStorage() {
    return {
      markdown: {
        serialize(state: { write: (text: string) => void; closeBlock: (node: unknown) => void }, node: { attrs: { src: string; title: string } }) {
          const title = node.attrs.title || '▶ Video abspielen'
          state.write(`[${title}](${node.attrs.src})`)
          state.closeBlock(node)
        },
      },
    }
  },
})
