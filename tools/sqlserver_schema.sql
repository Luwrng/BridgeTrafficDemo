-- Cấu trúc 6: chỉ tên bảng/cột tiếng Việt không dấu, giá trị giữ nguyên.
-- Chỉ tạo mới khi chưa có; DB cấu trúc 5 dùng localize_sqlserver.py để nâng cấp.
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.LuotXeRaVao', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.LuotXeRaVao (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_LuotXeRaVao PRIMARY KEY,
        LoaiDuLieu varchar(16) NOT NULL CONSTRAINT MacDinh_LuotXeRaVao_LoaiDuLieu DEFAULT 'real',
        BienSo varchar(16) NOT NULL,
        ThoiGianVao datetimeoffset(3) NULL, ThoiGianRa datetimeoffset(3) NULL,
        CongVao char(1) NULL, CongRa char(1) NULL,
        TrangThai varchar(24) NOT NULL, GhiChu nvarchar(1000) NULL,
        ThoiGianTao datetimeoffset(3) NOT NULL,
        MaNguonVao varchar(64) NULL, ThoiGianXoa datetimeoffset(3) NULL
    );
    CREATE INDEX ChiMuc_LuotXeRaVao_BienSo ON dbo.LuotXeRaVao(LoaiDuLieu,BienSo,TrangThai) INCLUDE(ThoiGianVao,ThoiGianRa,CongVao);
    CREATE INDEX ChiMuc_LuotXeRaVao_ThoiGianTao ON dbo.LuotXeRaVao(LoaiDuLieu,ThoiGianTao DESC);
END;
IF OBJECT_ID(N'dbo.LanGhiNhanCamera', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.LanGhiNhanCamera (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_LanGhiNhanCamera PRIMARY KEY,
        MaLuotXe varchar(32) NOT NULL CONSTRAINT KhoaNgoai_LanGhiNhanCamera_MaLuotXe REFERENCES dbo.LuotXeRaVao(Ma),
        MaSuKien nvarchar(450) COLLATE Latin1_General_100_BIN2 NOT NULL CONSTRAINT DuyNhat_LanGhiNhanCamera_MaSuKien UNIQUE,
        LoaiGhiNhan varchar(16) NOT NULL, MaCamera char(1) NOT NULL,
        ThoiGianGhiNhan datetimeoffset(3) NOT NULL,
        LoaiNguon varchar(16) NOT NULL, TenNguon nvarchar(200) NOT NULL,
        ThoiDiemVideo float NOT NULL, DoTinCay float NOT NULL,
        TepAnhToanCanh varchar(80) NOT NULL, TepAnhBienSo varchar(80) NOT NULL,
        MaKiemTraAnhToanCanh char(64) NOT NULL, MaKiemTraAnhBienSo char(64) NOT NULL
    );
    CREATE INDEX ChiMuc_LanGhiNhanCamera_MaLuotXe ON dbo.LanGhiNhanCamera(MaLuotXe,ThoiGianGhiNhan);
END;
IF OBJECT_ID(N'dbo.TrangThaiKhuD', N'U') IS NULL
    CREATE TABLE dbo.TrangThaiKhuD (
        Ma int NOT NULL CONSTRAINT KhoaChinh_TrangThaiKhuD PRIMARY KEY CONSTRAINT DieuKien_TrangThaiKhuD_Ma CHECK(Ma=1),
        PhienBanTrangThai bigint NOT NULL CONSTRAINT MacDinh_TrangThaiKhuD_PhienBanTrangThai DEFAULT 0
    );
IF NOT EXISTS (SELECT 1 FROM dbo.TrangThaiKhuD WHERE Ma=1)
    INSERT INTO dbo.TrangThaiKhuD(Ma,PhienBanTrangThai) VALUES(1,0);
IF OBJECT_ID(N'dbo.ChongGhiTrungBienSo', N'U') IS NULL
    CREATE TABLE dbo.ChongGhiTrungBienSo (
        BienSo varchar(16) NOT NULL CONSTRAINT KhoaChinh_ChongGhiTrungBienSo PRIMARY KEY,
        ThoiGianThayCuoi datetimeoffset(3) NOT NULL
    );
IF OBJECT_ID(N'dbo.DieuChinhLuotXe', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DieuChinhLuotXe (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_DieuChinhLuotXe PRIMARY KEY,
        MaLuotXe varchar(32) NOT NULL CONSTRAINT KhoaNgoai_DieuChinhLuotXe_MaLuotXe REFERENCES dbo.LuotXeRaVao(Ma),
        ThaoTac varchar(32) NOT NULL, MaCamera char(1) NOT NULL,
        ThoiGianGhiNhan datetimeoffset(3) NOT NULL, ThoiGianXacNhan datetimeoffset(3) NOT NULL,
        LyDo nvarchar(500) NOT NULL
    );
    CREATE INDEX ChiMuc_DieuChinhLuotXe_MaLuotXe ON dbo.DieuChinhLuotXe(MaLuotXe,ThoiGianXacNhan);
END;
IF OBJECT_ID(N'dbo.PhienBanCauTruc', N'U') IS NULL
    CREATE TABLE dbo.PhienBanCauTruc (
        PhienBan int NOT NULL CONSTRAINT KhoaChinh_PhienBanCauTruc PRIMARY KEY,
        ThoiGianCaiDat datetimeoffset(3) NOT NULL CONSTRAINT MacDinh_PhienBanCauTruc_ThoiGianCaiDat DEFAULT SYSDATETIMEOFFSET()
    );
IF NOT EXISTS (SELECT 1 FROM dbo.PhienBanCauTruc WHERE PhienBan=6)
    INSERT INTO dbo.PhienBanCauTruc(PhienBan) VALUES(6);
IF OBJECT_ID(N'dbo.NhatKyChuyenDuLieu', N'U') IS NULL
    CREATE TABLE dbo.NhatKyChuyenDuLieu (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_NhatKyChuyenDuLieu PRIMARY KEY,
        MaKiemTraNguon char(64) NOT NULL, DuongDanSaoLuu nvarchar(1000) NOT NULL,
        ThongKeSoDong nvarchar(max) NOT NULL,
        ThoiGianHoanThanh datetimeoffset(3) NOT NULL CONSTRAINT MacDinh_NhatKyChuyenDuLieu_ThoiGianHoanThanh DEFAULT SYSDATETIMEOFFSET()
    );
