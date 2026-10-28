import { createContext, useContext, type ReactNode } from 'react'

export interface CurrencyConfig {
  symbol: string
}

export const CURRENCIES: Record<string, CurrencyConfig> = {
  USD: { symbol: '$' },
}

// Pure money formatter: takes an already-converted amount and a symbol.
// No hardcoded rates, no global currency state.
export function formatMoney(amount: number, symbol = "$", maxFractionDigits = 2): string {
  const digits = symbol === "¥" || symbol === "₩" ? 0 : maxFractionDigits
  return `${symbol}${amount.toLocaleString(undefined, {
    minimumFractionDigits: digits > 0 ? 2 : 0,
    maximumFractionDigits: digits,
  })}`
}

interface PreferencesContextType {
  formatCurrency: (amountUSD: number) => string
}

const PreferencesContext = createContext<PreferencesContextType | undefined>(undefined)

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const formatCurrency = (amountUSD: number): string => {
    return formatMoney(amountUSD, '$')
  }

  return (
    <PreferencesContext.Provider
      value={{
        formatCurrency,
      }}
    >
      {children}
    </PreferencesContext.Provider>
  )
}

export function usePreferences() {
  const ctx = useContext(PreferencesContext)
  if (!ctx) throw new Error('usePreferences must be used within a PreferencesProvider')
  return ctx
}
