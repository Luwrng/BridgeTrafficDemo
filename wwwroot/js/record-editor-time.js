// Chuyển ô ngày giờ UTC+7 sang UTC, giữ mili giây gốc nếu người dùng không
// thay thời điểm. Tránh mất độ chính xác khi chỉ sửa biển số hoặc ghi chú.
function recordEditorTimestamp(value, original) {
  if (!value) return null;
  const changed = new Date(value + '+07:00').toISOString();
  if (original && Date.parse(changed) === Math.floor(Date.parse(original) / 1000) * 1000) return original;
  return changed;
}
if (typeof module !== 'undefined' && module.exports) module.exports = recordEditorTimestamp;
else window.recordEditorTimestamp = recordEditorTimestamp;
