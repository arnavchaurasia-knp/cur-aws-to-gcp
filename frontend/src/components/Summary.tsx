import ReactMarkdown from 'react-markdown'

export function Summary({ markdown }: { markdown: string }) {
  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-3">
        AI summary
      </h3>
      <div className="prose prose-invert prose-sm max-w-none text-gray-300
        prose-headings:text-white prose-headings:font-semibold
        prose-strong:text-white prose-a:text-[#645DF6] hover:prose-a:text-[#7d77f8]
        prose-code:text-[#00C2BB] prose-code:bg-white/5 prose-code:px-1 prose-code:rounded
        prose-li:my-0.5 prose-p:my-2 prose-ul:my-2 prose-ol:my-2">
        <ReactMarkdown>{markdown}</ReactMarkdown>
      </div>
    </div>
  )
}
