import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { getAdminNotifications, type NotificationItem } from '../api/admin'

const NOTIF_STORAGE_KEY = 'cur_admin_notif_last_viewed'
const NOTIF_EVENT_NAME = 'cur:notifications-refresh'

export interface NotificationsContextValue {
  feed: NotificationItem[]
  unreadCount: number
  loading: boolean
  lastViewedTimestamp: number
  refresh: () => Promise<void>
  markAllAsRead: () => void
}

const NotificationsContext = createContext<NotificationsContextValue | null>(null)

/**
 * Global helper to trigger an immediate background refresh of notifications
 * from anywhere in the app (e.g. after mutating assumptions, settings, tokens, or jobs).
 */
export function triggerNotificationRefresh() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(NOTIF_EVENT_NAME))
  }
}

interface NotificationProviderProps {
  children: ReactNode
  isAdmin?: boolean
}

export function NotificationProvider({ children, isAdmin = true }: NotificationProviderProps) {
  const location = useLocation()
  const [feed, setFeed] = useState<NotificationItem[]>([])
  const [loading, setLoading] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [lastViewedTimestamp, setLastViewedTimestamp] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(NOTIF_STORAGE_KEY)
      return saved ? parseInt(saved, 10) : 0
    } catch {
      return 0
    }
  })

  const isFetchingRef = useRef(false)

  const calculateUnread = useCallback((items: NotificationItem[], viewedTs: number) => {
    if (viewedTs > 0) {
      return items.filter(
        item => new Date(item.timestamp).getTime() > viewedTs
      ).length
    }
    return Math.min(items.length, 5)
  }, [])

  const refresh = useCallback(async () => {
    if (!isAdmin || isFetchingRef.current) return
    isFetchingRef.current = true
    setLoading(true)
    try {
      const data = await getAdminNotifications()
      const items = data.feed || []
      setFeed(items)
      setUnreadCount(calculateUnread(items, lastViewedTimestamp))
    } catch {
      // Quiet fail on background poll
    } finally {
      setLoading(false)
      isFetchingRef.current = false
    }
  }, [isAdmin, lastViewedTimestamp, calculateUnread])

  const markAllAsRead = useCallback(() => {
    const now = Date.now()
    setLastViewedTimestamp(now)
    setUnreadCount(0)
    try {
      localStorage.setItem(NOTIF_STORAGE_KEY, now.toString())
    } catch {
      // ignore
    }
  }, [])

  // 1. Refresh whenever route changes (instant responsiveness on navigation)
  useEffect(() => {
    if (isAdmin) {
      refresh()
    }
  }, [location.pathname, isAdmin, refresh])

  // 2. Refresh on custom event (triggered by mutations anywhere in the app)
  useEffect(() => {
    const handleEvent = () => {
      refresh()
    }
    window.addEventListener(NOTIF_EVENT_NAME, handleEvent)
    return () => window.removeEventListener(NOTIF_EVENT_NAME, handleEvent)
  }, [refresh])

  // 3. Sync across tabs or storage updates
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === NOTIF_STORAGE_KEY && e.newValue) {
        const nextTs = parseInt(e.newValue, 10)
        setLastViewedTimestamp(nextTs)
        setUnreadCount(calculateUnread(feed, nextTs))
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [feed, calculateUnread])

  // 4. Refresh when user focuses the tab or returns to window
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && isAdmin) {
        refresh()
      }
    }
    window.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('focus', handleVisibility)
    return () => {
      window.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('focus', handleVisibility)
    }
  }, [isAdmin, refresh])

  // 5. Background polling (12s interval for near-instant job/failure detection)
  useEffect(() => {
    if (!isAdmin) return
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }, 12000)
    return () => clearInterval(timer)
  }, [isAdmin, refresh])

  const value = {
    feed,
    unreadCount,
    loading,
    lastViewedTimestamp,
    refresh,
    markAllAsRead,
  }

  return (
    <NotificationsContext.Provider value={value}>
      {children}
    </NotificationsContext.Provider>
  )
}

/**
 * Hook to consume live notifications state and actions in any component.
 */
export function useNotifications(): NotificationsContextValue {
  const ctx = useContext(NotificationsContext)
  if (!ctx) {
    throw new Error('useNotifications must be used within a NotificationProvider')
  }
  return ctx
}

