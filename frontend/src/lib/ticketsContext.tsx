import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { listTickets, type Ticket } from '../api/admin'

const TICKETS_EVENT_NAME = 'cur:tickets-refresh'
const NOTIF_EVENT_NAME = 'cur:notifications-refresh'

export interface TicketsContextValue {
  tickets: Ticket[]
  openTickets: Ticket[]
  openCount: number
  loading: boolean
  refresh: () => Promise<void>
}

const TicketsContext = createContext<TicketsContextValue | null>(null)

/**
 * Global helper to trigger an immediate background refresh of tickets
 * from anywhere in the app (e.g. after creating, resolving, or deleting a ticket).
 */
export function triggerTicketsRefresh() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(TICKETS_EVENT_NAME))
  }
}

interface TicketsProviderProps {
  children: ReactNode
}

export function TicketsProvider({ children }: TicketsProviderProps) {
  const location = useLocation()
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [loading, setLoading] = useState(false)
  const isFetchingRef = useRef(false)

  const refresh = useCallback(async () => {
    if (isFetchingRef.current) return
    isFetchingRef.current = true
    setLoading(true)
    try {
      const res = await listTickets()
      setTickets(res.tickets || [])
    } catch {
      // Quiet fail on background poll
    } finally {
      setLoading(false)
      isFetchingRef.current = false
    }
  }, [])

  // 1. Refresh whenever route changes
  useEffect(() => {
    refresh()
  }, [location.pathname, refresh])

  // 2. Refresh on custom event (triggered by ticket creation, mutation, or notification refresh)
  useEffect(() => {
    const handleEvent = () => {
      refresh()
    }
    window.addEventListener(TICKETS_EVENT_NAME, handleEvent)
    window.addEventListener(NOTIF_EVENT_NAME, handleEvent)
    return () => {
      window.removeEventListener(TICKETS_EVENT_NAME, handleEvent)
      window.removeEventListener(NOTIF_EVENT_NAME, handleEvent)
    }
  }, [refresh])

  // 3. Refresh when user focuses the tab or returns to window
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }
    window.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('focus', handleVisibility)
    return () => {
      window.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('focus', handleVisibility)
    }
  }, [refresh])

  // 4. Background polling (15s interval for near-instant updates)
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [refresh])

  const openTickets = tickets.filter(t => t.status !== 'resolved' && t.status !== 'closed')
  const openCount = openTickets.length

  const value: TicketsContextValue = {
    tickets,
    openTickets,
    openCount,
    loading,
    refresh,
  }

  return (
    <TicketsContext.Provider value={value}>
      {children}
    </TicketsContext.Provider>
  )
}

/**
 * Hook to consume live tickets state and actions in any component.
 */
export function useTickets(): TicketsContextValue {
  const ctx = useContext(TicketsContext)
  if (!ctx) {
    throw new Error('useTickets must be used within a TicketsProvider')
  }
  return ctx
}
