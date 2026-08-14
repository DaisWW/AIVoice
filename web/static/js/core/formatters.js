const shortDateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "--";
  const value = Math.max(0, Math.round(Number(seconds)));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  if (minutes < 60) return `${minutes} 分 ${remainder} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export function formatDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : shortDateFormatter.format(date);
}

export function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function statusText(status) {
  return ({
    queued: "排队中",
    running: "生成中",
    completed: "已完成",
    failed: "有错误",
  })[status] || status;
}

export function normalizedStatus(status) {
  return ["queued", "running", "completed", "failed"].includes(status)
    ? status
    : "";
}

export function isActiveJob(job) {
  return job.status === "queued" || job.status === "running";
}

export function safeProgress(job) {
  return Math.min(100, Math.max(0, Number(job.progress || 0)));
}

export function jobHint(job) {
  if (job.status === "queued") {
    const position = job.queue_position ? `队列 #${job.queue_position}` : "排队中";
    return `${position} · 等待 ${formatDuration(job.estimated_wait_seconds)}`;
  }
  if (job.status === "running") {
    return `剩余约 ${formatDuration(job.eta_seconds ?? job.estimated_wait_seconds)}`;
  }
  if (job.status === "failed") return "部分或全部生成失败";
  return formatDate(job.finished_at);
}
