const FIVE_MINUTES_MS = 5 * 60 * 1000;
const WEEKDAYS = [
  "星期日",
  "星期一",
  "星期二",
  "星期三",
  "星期四",
  "星期五",
  "星期六",
] as const;

const pad = (value: number): string => String(value).padStart(2, "0");

const parseDate = (isoString: string): Date | null => {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date;
};

const isSameLocalDay = (left: Date, right: Date): boolean =>
  left.getFullYear() === right.getFullYear() &&
  left.getMonth() === right.getMonth() &&
  left.getDate() === right.getDate();

const formatClock = (date: Date): string => `${pad(date.getHours())}:${pad(date.getMinutes())}`;

export function formatMessageTime(isoString: string): string {
  const date = parseDate(isoString);
  if (date === null) {
    return "";
  }

  const now = new Date();
  if (isSameLocalDay(date, now)) {
    return formatClock(date);
  }

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (isSameLocalDay(date, yesterday)) {
    return `昨天 ${formatClock(date)}`;
  }

  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日 ${formatClock(date)}`;
  }

  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${formatClock(date)}`;
}

export function toTimestampMs(isoString: string | null | undefined): number | null {
  if (!isoString) {
    return null;
  }
  const date = parseDate(isoString);
  if (date === null) {
    return null;
  }
  return date.getTime();
}

export function formatTimeSeparatorLabel(
  isoString: string,
  previousIsoString?: string,
): string {
  const current = parseDate(isoString);
  if (current === null) {
    return "";
  }
  const previous = previousIsoString ? parseDate(previousIsoString) : null;
  if (previous === null || !isSameLocalDay(current, previous)) {
    return `${current.getMonth() + 1}月${current.getDate()}日 ${WEEKDAYS[current.getDay()]}`;
  }
  return formatClock(current);
}

export function shouldInsertTimeSeparator(
  isoString: string | null | undefined,
  previousIsoString?: string,
): boolean {
  if (!isoString) {
    return false;
  }
  if (!previousIsoString) {
    return true;
  }

  const currentMs = toTimestampMs(isoString);
  const previousMs = toTimestampMs(previousIsoString);
  if (currentMs === null || previousMs === null) {
    return currentMs !== null;
  }

  const current = parseDate(isoString);
  const previous = parseDate(previousIsoString);
  if (current === null || previous === null) {
    return false;
  }

  return !isSameLocalDay(current, previous) || currentMs - previousMs > FIVE_MINUTES_MS;
}
