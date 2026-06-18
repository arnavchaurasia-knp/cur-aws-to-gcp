// frontend/src/components/JobList.tsx
import type { Job } from '../api/jobs'
import { useNavigate } from 'react-router-dom'

const statusColor: Record<string, string> = {
  pending: 'text-gray-400',
  running: 'text-[#00C2BB]',
  done: 'text-[#00C2BB]',
  failed: 'text-orange-400'
}

export function JobList({ jobs }: { jobs: Job[] }) {
  const nav = useNavigate()
  if (!jobs.length) return <p className="text-sm text-gray-500">No reports yet</p>
  return (
    <ul className="divide-y divide-white/10 text-sm">
      {jobs.map(j => (
        <li key={j.id} className="flex justify-between items-center py-2 cursor-pointer hover:bg-white/5 px-1 rounded"
            onClick={() => nav(`/jobs/${j.id}`)}>
          <span className="text-gray-200">{j.prospect}</span>
          <span className={statusColor[j.status] ?? 'text-gray-400'}>{j.status}</span>
        </li>
      ))}
    </ul>
  )
}
