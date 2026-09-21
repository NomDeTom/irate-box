// Shared age formatting for anything driven by the uptime clock.
//
// The server sends a `now` tick count alongside every list, and each item carries the
// tick count it was created at. Ages are always computed as a difference: ticks are a
// duration, not a date, and there is no wall clock on this hardware to anchor them to.
// Never pass one to `new Date()`.

function formatAge(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (hours < 24) return remMinutes ? `${hours}h ${remMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours ? `${days}d ${remHours}h` : `${days}d`;
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
