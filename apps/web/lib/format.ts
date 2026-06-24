const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const numberFormatter = new Intl.NumberFormat('en-US');

export function formatMoney(cents: number) {
  return moneyFormatter.format(cents / 100);
}

export function formatPercent(value: number) {
  return `${value.toFixed(1)}%`;
}

export function formatCount(value: number) {
  return numberFormatter.format(value);
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${value}T00:00:00Z`));
}

export function formatDateTime(value: string) {
  const hasTimezone = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(value);
  const timestamp = hasTimezone ? value : `${value}Z`;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
  }).format(new Date(timestamp));
}

export function formatScenario(value: string | null) {
  return value ? value.replaceAll('_', ' ') : 'routine signal';
}
