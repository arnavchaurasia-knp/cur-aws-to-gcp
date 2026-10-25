import { useEffect, useState } from 'react'
import { Layout } from '../components/Layout'
import { listUploadedBills, type UploadedBill } from '../api/admin'
import { getNotificationSettings, updateNotificationSettings, type NotificationSettings } from '../api/auth'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'

function fmtBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export function Bills({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Uploaded Bills — admin')
  const [bills, setBills] = useState<UploadedBill[]>([])
  const [error, setError] = useState<string | null>(null)
  const [settings, setSettings] = useState<NotificationSettings | null>(null)
  const [savingSettings, setSavingSettings] = useState(false)

  const isAdmin = Boolean(user?.is_admin)

  const refreshBills = async () => {
    setError(null)
    try {
      const res = await listUploadedBills()
      setBills(res.bills)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load bills')
    }
  }

  const refreshSettings = async () => {
    try {
      const s = await getNotificationSettings()
      setSettings(s)
    } catch {
      // Non-fatal — settings section will just show disabled
    }
  }

  useEffect(() => {
    refreshBills()
    refreshSettings()
  }, [])

  const handleToggle = async (key: keyof NotificationSettings) => {
    if (!settings) return
    setSavingSettings(true)
    try {
      const next = { ...settings, [key]: !settings[key] }
      const updated = await updateNotificationSettings(next)
      setSettings(updated)
      toast.success(`Notifications updated: ${key} is now ${updated[key] ? 'on' : 'off'}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to update notifications')
    } finally {
      setSavingSettings(false)
    }
  }

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6 mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Uploaded Bills</h1>
            <p className="text-sm text-gray-400 mt-1">
              All AWS bill files uploaded to the system. Admin can view and manage notification preferences below.
            </p>
          </div>

          {/* Notification Settings Panel */}
          <div
            className={`bg-white/[0.02] border border-white/10 rounded-xl p-5 space-y-3 ${settings ? '' : 'opacity-50 pointer-events-none'}`}
          >
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
              Email & Slack Notifications
            </h2>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-300">Email notifications</span>
              <button
                onClick={() => handleToggle('email_notifications')}
                disabled={savingSettings}
                className={`relative inline-flex w-12 h-6 rounded-full transition-colors ${
                  settings?.email_notifications ? 'bg-[#00C2BB]' : 'bg-white/10'
                }`}
              >
                <span
                  className={`block w-4 h-4 rounded-full bg-white shadow transform transition-transform ${
                    settings?.email_notifications ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
              <span className={`text-xs font-medium ${settings?.email_notifications ? 'text-[#00C2BB]' : 'text-gray-400'}`}>
                {settings?.email_notifications ? 'On' : 'Off'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-300">Slack notifications</span>
              <button
                onClick={() => handleToggle('slack_notifications')}
                disabled={savingSettings}
                className={`relative inline-flex w-12 h-6 rounded-full transition-colors ${
                  settings?.slack_notifications ? 'bg-[#00C2BB]' : 'bg-white/10'
                }`}
              >
                <span
                  className={`block w-4 h-4 rounded-full bg-white shadow transform transition-transform ${
                    settings?.slack_notifications ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
              <span className={`text-xs font-medium ${settings?.slack_notifications ? 'text-[#00C2BB]' : 'text-gray-400'}`}>
                {settings?.slack_notifications ? 'On' : 'Off'}
              </span>
            </div>
            {settings && (
              <p className="text-[10px] text-gray-500 mt-1">
                When email is on, you'll receive a notification when your report is ready.
              </p>
            )}
          </div>
        </div>

        {error && (
          <div className="mb-6 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        {/* Bills Table */}
        <div className="border border-white/10 rounded-lg overflow-hidden">
          <div className="bg-white/[0.02] border-b border-white/10 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Uploaded Bills</h2>
            <p className="text-xs text-gray-500 mt-1">
              {bills.length} bill{bills.length !== 1 ? 's' : ''} uploaded
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">File Name</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Type</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Size</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Uploaded At</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5 text-right">Content Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {bills.length === 0 ? (
                  <tr>
                    <td className="px-5 py-6 text-center text-gray-500 text-xs" colSpan={5}>
                      No bills uploaded yet.
                    </td>
                  </tr>
                ) : (
                  bills.map(b => (
                    <tr key={b.content_hash} className="hover:bg-white/[0.01] transition-colors">
                      <td className="px-5 py-4 text-gray-200 font-medium">{b.file_name}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs uppercase">{b.input_ext}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs">{fmtBytes(b.file_size)}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs whitespace-nowrap">
                        {new Date(b.created_at).toLocaleString()}
                      </td>
                      <td className="px-5 py-4 text-gray-400 text-xs font-mono text-right truncate max-w-[200px]" title={b.content_hash}>
                        {b.content_hash.slice(0, 16)}…
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {!isAdmin && (
          <div className="mt-6 p-4 rounded-lg bg-amber-950/20 border border-amber-500/30 text-amber-400 text-xs">
            <p className="font-medium">Admin access required</p>
            <p className="text-[10px] text-amber-400/70 mt-1">
              Only administrators can view all uploaded bills and manage notification settings.
            </p>
          </div>
        )}
      </div>
    </Layout>
  )
}
