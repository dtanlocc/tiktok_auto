-- Kho license tren Supabase.
--
-- ⛔ BANG NAM O SCHEMA `license`, KHONG PHAI `public`, VA DO LA PHAN QUAN
-- TRONG NHAT CUA FILE NAY. PostgREST cua Supabase chi phuc vu nhung schema
-- duoc khai trong `db-schemas` (mac dinh: public, graphql_public). Anon key
-- duoc NHUNG VAO APP KHACH - no se lo, coi nhu da lo tu ngay phat hanh - nen
-- bat ky bang nao trong `public` deu la mot endpoint HTTP cong khai cho tat ca
-- ai co file .exe. Dat o `license` thi PostgREST khong nhin thay duong nao toi
-- no: khong phai "bi tu choi", ma la "khong ton tai" tu goc nhin cua API.
--
-- RLS ben duoi la lop thu hai, khong phai lop duy nhat. Dung mot minh RLS o
-- `public` thi mot policy viet sai mot lan la lo sach bang key.
create schema if not exists license;

-- ⛔ CAC ROLE NAY CHI TON TAI TREN SUPABASE. `anon`, `authenticated`,
-- `service_role` do Supabase tao san; mot Postgres thuong khong co chung, va
-- `revoke ... from anon` o do la loi "role does not exist" lam DUNG CA
-- MIGRATION. Boc trong DO block de file nay ap duoc len ca Postgres trong -
-- khong phai de cho tien, ma de bo SQL nay CO THE DUOC TEST o mot noi khong
-- phai du an that cua khach.
do $$
declare
    role_name text;
begin
    foreach role_name in array array['anon', 'authenticated']
    loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('revoke all on schema license from %I', role_name);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        execute 'grant usage on schema license to service_role';
    end if;
end
$$;

-- =============================================================================
-- licenses
-- -----------------------------------------------------------------------------
-- ⛔ KHONG LUU KEY THAT. `key_digest` = HMAC-SHA256(pepper, key da chuan hoa),
-- giong `control_plane/app/security.py:digest_license_key`. Doc duoc ca bang
-- nay cung khong dung lai duoc key nao, vi pepper khong nam trong database -
-- no nam trong secret cua Edge Function. Day la ly do bang nay chiu duoc viec
-- bi doc, va la ly do pepper KHONG duoc luu o day cho tien.
-- =============================================================================
create table if not exists license.licenses (
    id                 text primary key,
    key_digest         text not null unique,
    customer_reference text not null default '',
    plan               text not null,
    -- ⛔ `features` LA NGUON SU THAT DUY NHAT CHO NHOM VA QUYEN. Group va role
    -- di ben trong features duoi dang `group.<id>` / `role.<ten>` vi
    -- LeaseClaims la extra="forbid" va frozen (xem
    -- backend/app/security/license.py). Khong them cot group_id rieng o day:
    -- hai nguon su that se lech nhau, va cai duoc KY la features.
    -- Muon xem theo nhom thi dung view `license.overview` ben duoi.
    features           jsonb not null default '[]'::jsonb,
    max_accounts       integer not null check (max_accounts between 1 and 1000000),
    max_tabs           integer not null check (max_tabs between 1 and 1000),
    max_devices        integer not null check (max_devices between 1 and 1000),
    channel            text not null default 'stable'
                       check (channel in ('internal', 'beta', 'stable')),
    expires_at         bigint not null check (expires_at >= 0),
    created_at         bigint not null default extract(epoch from now())::bigint,
    revoked_at         bigint,
    revoked_reason     text,
    constraint features_is_array check (jsonb_typeof(features) = 'array')
);

-- =============================================================================
-- devices
-- -----------------------------------------------------------------------------
-- id = `device_<40 hex>` = sha256(khoa cong khai thiet bi)[..40]. Khach khong
-- tu dat duoc id: Edge Function tinh lai tu khoa cong khai gui len va tu choi
-- neu khong khop (enforceDeviceId).
-- =============================================================================
create table if not exists license.devices (
    id                  text primary key,
    license_id          text not null references license.licenses(id) on delete cascade,
    public_key_pem      text not null,
    first_activated_at  bigint not null default extract(epoch from now())::bigint,
    last_seen_at        bigint not null default extract(epoch from now())::bigint,
    revoked_at          bigint
);
create index if not exists devices_license_idx on license.devices (license_id);

-- =============================================================================
-- used_nonces - chong phat lai device proof
-- -----------------------------------------------------------------------------
-- ⛔ CHONG PHAT LAI PHAI LA MOT INSERT, KHONG PHAI "SELECT roi INSERT". Hai
-- request cung nonce toi cung luc deu thay bang trong o buoc SELECT roi deu
-- di tiep. `insert ... on conflict do nothing` de Postgres quyet dinh, va chi
-- dung mot ai thang. Ham ben duoi lam dung the.
-- =============================================================================
create table if not exists license.used_nonces (
    digest     text primary key,
    expires_at bigint not null
);
create index if not exists used_nonces_expiry_idx on license.used_nonces (expires_at);

-- =============================================================================
-- rate_limits
-- -----------------------------------------------------------------------------
-- ⛔ BAN PYTHON CO `activation_rate_limit_per_minute`; EDGE FUNCTION PHAI TU
-- LO LAY. Endpoint activate la cong khai (khach goi bang anon key), va
-- Supabase khong gioi han tan so Edge Function theo IP. Thieu bang nay thi
-- viec do key bang cach thu lien tuc la mien phi va khong ai thay.
-- =============================================================================
create table if not exists license.rate_limits (
    bucket       text not null,
    window_start bigint not null,
    hits         integer not null default 0,
    primary key (bucket, window_start)
);
create index if not exists rate_limits_window_idx on license.rate_limits (window_start);

-- =============================================================================
-- audit_events
-- =============================================================================
create table if not exists license.audit_events (
    id          bigserial primary key,
    event_type  text not null,
    actor_id    text not null default '',
    subject_id  text not null default '',
    metadata    jsonb not null default '{}'::jsonb,
    created_at  bigint not null default extract(epoch from now())::bigint
);
create index if not exists audit_subject_idx on license.audit_events (subject_id);
create index if not exists audit_created_idx on license.audit_events (created_at desc);

-- =============================================================================
-- RLS: bat, va KHONG co policy nao
-- -----------------------------------------------------------------------------
-- Bat RLS ma khong khai policy = tu choi tat ca moi role thuong. service_role
-- va chu bang di qua RLS, nen Edge Function (service_role) va CLI admin (ket
-- noi Postgres truc tiep) van lam viec binh thuong.
-- =============================================================================
alter table license.licenses     enable row level security;
alter table license.devices      enable row level security;
alter table license.used_nonces  enable row level security;
alter table license.rate_limits  enable row level security;
alter table license.audit_events enable row level security;

-- =============================================================================
-- view de quan sat: khong co key, khong co khoa cong khai thiet bi
-- -----------------------------------------------------------------------------
-- Day la thu duoc doc khi anh muon nhin tu dien thoai: con bao nhieu ngay, may
-- thiet bi, thuoc nhom nao. `group_id` duoc RUT RA tu features chu khong luu
-- rieng, nen khong the lech voi thu da duoc ky.
-- =============================================================================
create or replace view license.overview as
select
    l.id,
    l.customer_reference,
    l.plan,
    l.channel,
    (
        select f.value #>> '{}'
        from jsonb_array_elements(l.features) as f(value)
        where f.value #>> '{}' like 'group.%'
        limit 1
    ) as group_feature,
    (
        select f.value #>> '{}'
        from jsonb_array_elements(l.features) as f(value)
        where f.value #>> '{}' like 'role.%'
        limit 1
    ) as role_feature,
    l.features,
    l.max_accounts,
    l.max_tabs,
    l.max_devices,
    (
        select count(*)
        from license.devices d
        where d.license_id = l.id and d.revoked_at is null
    ) as active_devices,
    l.expires_at,
    to_timestamp(l.expires_at) as expires_at_utc,
    greatest(0, (l.expires_at - extract(epoch from now())::bigint) / 86400) as days_left,
    l.revoked_at is not null as revoked,
    l.revoked_reason,
    to_timestamp(l.created_at) as created_at_utc
from license.licenses l;
