/** Reassemble one original command reply before exposing any of its value. */
export class ReplyFragments {
  private parts: string[] = []
  private offset = 0
  private total: number | undefined
  private complete = false
  append(fragment: unknown): { complete: false } | { complete: true; value: any } {
    if (this.complete || !fragment || typeof fragment !== 'object' || Array.isArray(fragment)) {
      throw new Error('invalid reply fragment')
    }
    const page = fragment as Record<string, any>
    const fields = ['schema_version', 'offset_chars', 'next_offset_chars', 'total_chars', 'content']
    if (Object.keys(page).length !== fields.length || fields.some(key => !(key in page))
      || page.schema_version !== 'rpnh/dsh-reply-fragment/v1'
      || !Number.isSafeInteger(page.offset_chars) || page.offset_chars !== this.offset
      || !Number.isSafeInteger(page.total_chars) || page.total_chars < 1
      || (this.total !== undefined && page.total_chars !== this.total)
      || typeof page.content !== 'string' || !page.content.length
      // The Python transport serializes the original result as ASCII JSON.
      || /[^\x00-\x7f]/.test(page.content)) throw new Error('reply fragment scope or offset differs')
    const end = this.offset + page.content.length
    if (!Number.isSafeInteger(end) || end > page.total_chars
      || (end < page.total_chars ? page.next_offset_chars !== end : page.next_offset_chars !== null)) {
      throw new Error('reply fragment continuation differs')
    }
    this.parts.push(page.content); this.offset = end; this.total = page.total_chars
    if (end !== this.total) return { complete: false }
    // Parse before returning the only complete value. A broken stream never
    // exposes a partial history or an apparent completed business operation.
    const value = JSON.parse(this.parts.join(''))
    this.complete = true; this.parts = []
    return { complete: true, value }
  }
}
