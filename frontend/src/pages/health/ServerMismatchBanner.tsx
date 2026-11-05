import { EXPECTED_HEALTH_SCHEMA } from '../../api/health'

interface ServerMismatchBannerProps {
  serverSchemaVersion: number
}

export function ServerMismatchBanner({ serverSchemaVersion }: Readonly<ServerMismatchBannerProps>) {
  if (serverSchemaVersion === EXPECTED_HEALTH_SCHEMA) {
    return null
  }

  return (
    <div className="p-4 rounded-xl bg-rose-600 text-white shadow-md flex items-start gap-3">
      <span className="text-xl leading-none">⚠️</span>
      <div className="text-sm space-y-1">
        <strong className="block font-semibold">Backend version mismatch detected</strong>
        <p className="opacity-95 text-xs leading-relaxed">
          This page is newer than the backend it's talking to (expected health schema v
          {EXPECTED_HEALTH_SCHEMA}, but backend reported v{serverSchemaVersion}). Restart the backend (
          <code className="bg-rose-700/80 px-1 py-0.5 rounded font-mono">go run ./cmd/server</code>) or
          redeploy — until then some checks are missing or wrong.
        </p>
      </div>
    </div>
  )
}
