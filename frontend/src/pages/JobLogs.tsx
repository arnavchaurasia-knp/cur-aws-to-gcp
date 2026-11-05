import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { BackButton } from '../components/BackButton'
import { JobLogsViewer } from '../components/JobLogsViewer'
import { getJob } from '../api/jobs'
import type { Job } from '../api/jobs'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

export function JobLogs({ user }: Readonly<{ user: UserInfo }>) {
  const { id, stream } = useParams<{ id: string; stream?: string }>()
  const [job, setJob] = useState<Job | null>(null)
  
  useTitle(job ? `Logs — ${job.prospect}` : 'Job Logs')

  useEffect(() => {
    if (!id) return
    getJob(id)
      .then(setJob)
      .catch(() => setJob(null))
      
  }, [id])

  const validStream = (stream === 'pipeline' || stream === 'engine' || stream === 'diagnostics')
    ? stream
    : 'pipeline'

  return (
    <Layout user={user}>
      <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 py-8 min-w-0 space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <BackButton fallback={id ? `/jobs/${id}` : '/'} />
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h1 className="text-2xl font-bold tracking-tight">
                  Execution Logs
                </h1>
                {job && (
                  <span className="text-base text-[var(--fcp-text-secondary)] font-medium">
                    · <span className="text-[#645DF6]">{job.prospect}</span>
                  </span>
                )}
                {id && (
                  <span className="text-xs px-2.5 py-0.5 rounded-md bg-white/[0.04] border border-white/10 font-mono text-gray-400">
                    {id.slice(0, 8)}
                  </span>
                )}
              </div>
              <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                Full-screen execution console, agent diagnostics, and orchestration telemetry
              </p>
            </div>
          </div>

          {id && (
            <Link
              to={`/jobs/${id}`}
              className="text-xs px-3 py-1.5 rounded-lg border border-white/10 hover:border-white/20 text-gray-300 hover:text-white transition-colors flex items-center gap-1.5 cursor-pointer"
            >
              <span>← Back to Report</span>
            </Link>
          )}
        </div>

        {id ? (
          <div className="anim-fade-in">
            <JobLogsViewer
              jobId={id}
              isRunning={job ? job.status === 'running' || job.status === 'pending' : false}
              initialStream={validStream}
            />
          </div>
        ) : (
          <div className="p-8 text-center text-sm text-gray-400">
            No job ID provided.
          </div>
        )}
      </div>
    </Layout>
  )
}
