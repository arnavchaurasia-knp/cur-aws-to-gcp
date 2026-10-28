// Pure money formatter: takes an already-converted amount and a currency symbol.
// No hardcoded FX rates, no global currency state — per-job currency comes from
// the backend (bill_currency / currency.json) and is applied at the call site.
export function formatMoney(amount: number, symbol = "$", maxFractionDigits = 2): string {
  const digits = symbol === "¥" || symbol === "₩" ? 0 : maxFractionDigits
  return `${symbol}${amount.toLocaleString(undefined, {
    minimumFractionDigits: digits > 0 ? 2 : 0,
    maximumFractionDigits: digits,
  })}`
}
