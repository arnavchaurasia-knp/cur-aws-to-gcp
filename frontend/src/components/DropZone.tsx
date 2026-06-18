// frontend/src/components/DropZone.tsx
import { useRef, useState } from 'react'

interface Props { onChange: (file: File) => void; file: File | null }

export function DropZone({ onChange, file }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const accept = (f: File) => {
    if (f.name.match(/\.(csv|zip|pdf)$/i)) onChange(f)
  }

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) accept(f) }}
      className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition
        ${dragging ? 'border-[#645DF6] bg-[#645DF6]/10' : 'border-white/20 hover:border-[#645DF6]/50'}`}
    >
      <input ref={inputRef} type="file" accept=".csv,.zip,.pdf" className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) accept(f) }} />
      {file
        ? <p className="text-sm text-gray-300">{file.name}</p>
        : <>
            <p className="text-sm text-gray-400">Drop CSV, ZIP, or PDF here</p>
            <p className="text-xs text-[#645DF6] mt-1">or click to browse</p>
          </>
      }
    </div>
  )
}
