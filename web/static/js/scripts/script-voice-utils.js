export function isVoiceUsable(voice) {
  return Number(voice?.enabled_file_count || 0) > 0;
}

export function voiceOptionLabel(voice) {
  return `${voice.name} · ${Number(voice.enabled_file_count || 0)} 条启用`;
}
