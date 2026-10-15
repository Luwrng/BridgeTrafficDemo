-- Nâng cấp bổ sung 6 -> 7. Không đổi tên/xóa cột cũ, không sửa lượt xe cũ.
IF COL_LENGTH(N'dbo.LuotXeRaVao',N'GhiChuBoSung') IS NULL
    ALTER TABLE dbo.LuotXeRaVao ADD GhiChuBoSung nvarchar(1000) NOT NULL CONSTRAINT MacDinh_LuotXeRaVao_GhiChuBoSung DEFAULT N'';
IF COL_LENGTH(N'dbo.LuotXeRaVao',N'LyDoCanKiemTra') IS NULL
    ALTER TABLE dbo.LuotXeRaVao ADD LyDoCanKiemTra nvarchar(1000) NOT NULL CONSTRAINT MacDinh_LuotXeRaVao_LyDoCanKiemTra DEFAULT N'';
IF COL_LENGTH(N'dbo.LuotXeRaVao',N'NguonTao') IS NULL
    ALTER TABLE dbo.LuotXeRaVao ADD NguonTao varchar(16) NOT NULL CONSTRAINT MacDinh_LuotXeRaVao_NguonTao DEFAULT 'camera';
IF COL_LENGTH(N'dbo.LuotXeRaVao',N'PhienBanChinhSua') IS NULL
    ALTER TABLE dbo.LuotXeRaVao ADD PhienBanChinhSua int NOT NULL CONSTRAINT MacDinh_LuotXeRaVao_PhienBanChinhSua DEFAULT 0;
IF OBJECT_ID(N'dbo.NhatKySuaHoSo',N'U') IS NULL
BEGIN
    CREATE TABLE dbo.NhatKySuaHoSo (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_NhatKySuaHoSo PRIMARY KEY,
        MaLuotXe varchar(32) NOT NULL CONSTRAINT KhoaNgoai_NhatKySuaHoSo_MaLuotXe REFERENCES dbo.LuotXeRaVao(Ma),
        ThaoTac varchar(32) NOT NULL, ThoiGianTao datetimeoffset(3) NOT NULL,
        LyDo nvarchar(500) NOT NULL, DuLieuTruocSua nvarchar(max) NOT NULL, DuLieuSauSua nvarchar(max) NOT NULL
    );
    CREATE INDEX ChiMuc_NhatKySuaHoSo_MaLuotXe ON dbo.NhatKySuaHoSo(MaLuotXe,ThoiGianTao);
END;
IF OBJECT_ID(N'dbo.AnhBoSungHoSo',N'U') IS NULL
BEGIN
    CREATE TABLE dbo.AnhBoSungHoSo (
        Ma varchar(32) NOT NULL CONSTRAINT KhoaChinh_AnhBoSungHoSo PRIMARY KEY,
        MaLuotXe varchar(32) NOT NULL CONSTRAINT KhoaNgoai_AnhBoSungHoSo_MaLuotXe REFERENCES dbo.LuotXeRaVao(Ma),
        LoaiGhiNhan varchar(16) NOT NULL,
        TenTepAnh varchar(80) NOT NULL CONSTRAINT DuyNhat_AnhBoSungHoSo_TenTepAnh UNIQUE,
        MaKiemTraAnh char(64) NOT NULL, ThoiGianTao datetimeoffset(3) NOT NULL,
        GhiChuBoSung nvarchar(500) NOT NULL
    );
    CREATE INDEX ChiMuc_AnhBoSungHoSo_MaLuotXe ON dbo.AnhBoSungHoSo(MaLuotXe,LoaiGhiNhan);
END;
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.LuotXeRaVao') AND name=N'ChiMuc_LuotXeRaVao_ThoiGianVao')
    CREATE INDEX ChiMuc_LuotXeRaVao_ThoiGianVao ON dbo.LuotXeRaVao(ThoiGianVao);
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.LuotXeRaVao') AND name=N'ChiMuc_LuotXeRaVao_ThoiGianRa')
    CREATE INDEX ChiMuc_LuotXeRaVao_ThoiGianRa ON dbo.LuotXeRaVao(ThoiGianRa);
IF NOT EXISTS(SELECT 1 FROM dbo.PhienBanCauTruc WHERE PhienBan=7)
    INSERT INTO dbo.PhienBanCauTruc(PhienBan) VALUES(7);
