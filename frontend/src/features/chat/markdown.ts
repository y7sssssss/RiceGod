import MarkdownIt from 'markdown-it'

// Model HTML is always escaped. Default markdown-it URL validation blocks unsafe protocols.
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true, typographer: false })
const defaultLink = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index]!.attrSet('target', '_blank')
  tokens[index]!.attrSet('rel', 'noopener noreferrer nofollow')
  return defaultLink ? defaultLink(tokens, index, options, env, self) : self.renderToken(tokens, index, options)
}
// Images can be tracking beacons. Show only their escaped alt text, never load model URLs.
markdown.renderer.rules.image = (tokens, index) => `<span class="markdown-image-alt">[图片：${markdown.utils.escapeHtml(tokens[index]?.content || '未加载')}]</span>`
export const renderMarkdown = (text: string): string => markdown.render(text)
export function safeSourceUrl(source: string): string | undefined {
  try {
    const url = new URL(source)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}
