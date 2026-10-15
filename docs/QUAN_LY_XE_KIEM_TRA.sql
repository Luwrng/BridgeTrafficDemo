-- Chỉ SELECT để kiểm tra. Không sửa hồ sơ hoặc trạng thái xe.
-- Kết nối .\SQLEXPRESS bằng Windows Authentication trong SSMS.
USE QuanLyXe;
GO
SELECT @@SERVERNAME AS MayChu, DB_NAME() AS DatabaseDangDung, ORIGINAL_LOGIN() AS TaiKhoan;
GO
-- Tên bảng/cột đã đổi; dữ liệu và các mã trạng thái giữ nguyên.
SELECT N'LuotXeRaVao' AS Bang, COUNT(*) AS SoDong FROM dbo.LuotXeRaVao
UNION ALL SELECT N'LanGhiNhanCamera', COUNT(*) FROM dbo.LanGhiNhanCamera
UNION ALL SELECT N'TrangThaiKhuD', COUNT(*) FROM dbo.TrangThaiKhuD
UNION ALL SELECT N'ChongGhiTrungBienSo', COUNT(*) FROM dbo.ChongGhiTrungBienSo
UNION ALL SELECT N'DieuChinhLuotXe', COUNT(*) FROM dbo.DieuChinhLuotXe
UNION ALL SELECT N'PhienBanCauTruc', COUNT(*) FROM dbo.PhienBanCauTruc
UNION ALL SELECT N'NhatKyChuyenDuLieu', COUNT(*) FROM dbo.NhatKyChuyenDuLieu;
GO
SELECT TOP(50) BienSo, CongVao,
    SWITCHOFFSET(ThoiGianVao,'+07:00') AS NgayGioVao,
    CongRa, SWITCHOFFSET(ThoiGianRa,'+07:00') AS NgayGioRa,
    TrangThai, ThoiGianXoa, GhiChu
FROM dbo.LuotXeRaVao
ORDER BY COALESCE(ThoiGianRa,ThoiGianVao,ThoiGianTao) DESC;
GO
SELECT TOP(50) LoaiGhiNhan, MaCamera,
    SWITCHOFFSET(ThoiGianGhiNhan,'+07:00') AS NgayGioGhiNhan, DoTinCay,
    TepAnhToanCanh, TepAnhBienSo, MaKiemTraAnhToanCanh, MaKiemTraAnhBienSo
FROM dbo.LanGhiNhanCamera ORDER BY ThoiGianGhiNhan DESC;
GO
SELECT * FROM dbo.TrangThaiKhuD;
SELECT * FROM dbo.DieuChinhLuotXe ORDER BY ThoiGianXacNhan DESC;
SELECT * FROM dbo.PhienBanCauTruc;
SELECT * FROM dbo.NhatKyChuyenDuLieu ORDER BY ThoiGianHoanThanh DESC;
SELECT name AS TenTep, type_desc AS LoaiTep, physical_name AS DuongDan FROM sys.database_files;
GO
-- Danh mục tên để kiểm tra trực tiếp trong SSMS.
SELECT t.name AS TenBang, c.column_id AS ThuTuCot, c.name AS TenCot,
    TYPE_NAME(c.user_type_id) AS KieuDuLieu, c.is_nullable AS ChoPhepRong
FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id
WHERE t.schema_id=SCHEMA_ID(N'dbo') ORDER BY t.name,c.column_id;
GO
