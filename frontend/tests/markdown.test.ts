import { describe, expect, it } from 'vitest'
import { renderMarkdown, safeSourceUrl } from '../src/features/chat/markdown'

describe('untrusted LLM markdown', () => {
  it('escapes HTML, scripts, event handlers and fenced markup', () => {
    const html = renderMarkdown('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n```html\n<script>alert(1)</script>\n```')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('<pre><code')
  })
  it('rejects executable links and does not load external images', () => {
    const html = renderMarkdown('[x](javascript:alert(1))\n![meal](https://example.com/tracker.png)\n[文档](https://example.com)')
    expect(html).not.toContain('href="javascript:')
    expect(html).not.toContain('<img')
    expect(html).toContain('noopener noreferrer nofollow')
    expect(safeSourceUrl('javascript:alert(1)')).toBeUndefined()
    expect(safeSourceUrl('//example.com')).toBeUndefined()
    expect(safeSourceUrl('Neo4j: 番茄炒蛋')).toBeUndefined()
    expect(safeSourceUrl('https://example.com/source')).toBe('https://example.com/source')
  })
  it('supports ordered lists and blockquotes', () => {
    const html = renderMarkdown('1. 洗菜\n2. 下锅\n\n> 注意火候')
    expect(html).toContain('<ol>')
    expect(html).toContain('<blockquote>')
  })
})
