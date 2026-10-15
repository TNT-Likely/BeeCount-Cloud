export function attachmentDateRange(year: number | null, month: number | null) {
  if (year === null) return {}
  return {
    dateFrom: new Date(year, month === null ? 0 : month - 1, 1).toISOString(),
    dateTo: new Date(year, month === null ? 12 : month, 1).toISOString(),
  }
}
