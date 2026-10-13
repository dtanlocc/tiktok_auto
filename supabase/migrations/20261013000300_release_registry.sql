-- So dang ky ban phat hanh + ve tai mot lan.
--
-- ⛔ VI SAO PHAI CO FILE NAY, va vi sao bo no la app KHONG CHAY DUOC CHUT NAO.
-- Duong duy nhat launcher lay backend binary la `check_backend_update`
-- (lib.rs:833) -> neu endpoint releases/check tra `update_available: false`
-- thi khong co gi duoc tai, va `launch_backend` goi `load_backend_pointer` roi
-- chet voi "Backend binary chua duoc cai." Khong phai sau khi het han - NGAY
-- LAN CHAY DAU. Ban dau du an chi co 20261013000200 (luon tra false) va do la
-- mot ban licensed khong bao gio khoi dong duoc.
--
-- ⛔ VA KHOA KY RELEASE VAN KHONG LEN MAY. Day la cho de nham nhat: manifest
-- duoc KY O MAY OPERATOR (`scripts/supabase_release_admin.py` dung
-- `ReleaseManifestSigner`), roi chi co CHUOI TOKEN DA KY duoc luu vao cot
-- `signed_manifest`. Edge Function phat lai token do, khong ky gi ca. Chiem
-- duoc ca database nay cung khong ky duoc mot manifest moi - tuc khong gui
-- duoc ban cai doc cho khach.
--
-- ⛔ `download_url` BEN TRONG MANIFEST PHAI LA URL CO DINH. No nam trong phan
-- da duoc ky nen khong sua duoc theo tung request; vi vay no tro vao function
-- `artifacts` (URL khong doi) chu khong tro thang vao signed URL cua Storage
-- (het han sau vai phut, va luc do manifest da ky thanh vo dung).

create table if not exists license.releases (
    id                       text primary key,
    component                text not null check (component in ('backend', 'desktop')),
    version                  text not null,
    channel                  text not null check (channel in ('internal', 'beta', 'stable')),
    target                   text not null check (target = 'windows-x86_64'),
    -- Duong dan trong bucket Storage, khong phai URL. URL duoc ky ngan han
    -- ngay luc tai, boi function `artifacts`.
    storage_object           text not null,
    sha256                   text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes               bigint not null check (size_bytes > 0),
    published_at             bigint not null default extract(epoch from now())::bigint,
    minimum_launcher_version text not null,
    mandatory                boolean not null default false,
    -- Token manifest DA KY o may operator. Nguon su that ve sha256/size doi
    -- voi launcher la token nay, khong phai cac cot ben tren: launcher kiem
    -- file tai ve bang chu ky, con cac cot chi de truy van.
    signed_manifest          text not null,
    active                   boolean not null default true,
    unique (component, version, channel, target)
);
create index if not exists releases_lookup_idx
    on license.releases (component, channel, target, active);

-- =============================================================================
-- ve tai dung MOT LAN
-- -----------------------------------------------------------------------------
-- Ban Python co bang `UsedDownloadGrant` voi cung muc dich. Khong co no thi
-- mot ve con han la mot URL tai lai duoc bao nhieu lan cung duoc trong vong
-- doi cua no - va ve do di qua tay khach.
-- =============================================================================
create table if not exists license.used_download_grants (
    digest     text primary key,
    expires_at bigint not null
);
create index if not exists used_grants_expiry_idx
    on license.used_download_grants (expires_at);

alter table license.releases            enable row level security;
alter table license.used_download_grants enable row level security;

-- =============================================================================
-- tkauto_release_check - thay ban luon tra "khong co gi"
-- -----------------------------------------------------------------------------
-- ⛔ TRA VE DANH SACH UNG VIEN, KHONG TU CHON. So sanh semver (ke ca
-- prerelease) trong plpgsql vua dai vua de sai, trong khi `compareSemver`
-- trong lease.ts DA duoc do tung truong hop voi `_compare_semver` cua Python
-- bang `test_supabase_lease_parity.py`. Dung lai cai da do tot hon viet ban
-- thu ba chua ai do.
--
-- Channel lay tu LICENSE chu khong tu request: khach khong tu chon duoc kenh
-- internal/beta cho minh. Giong ban Python.
-- =============================================================================
create or replace function public.tkauto_release_check(
    p_device_id    text,
    p_license_id   text,
    p_nonce_digest text,
    p_nonce_ttl    integer,
    p_rate_bucket  text,
    p_rate_limit   integer,
    p_rate_window  integer,
    p_component    text default 'backend',
    p_target       text default 'windows-x86_64'
) returns jsonb
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now     bigint := extract(epoch from now())::bigint;
    v_device  devices;
    v_license licenses;
    v_rows    jsonb;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    if not license.claim_nonce(p_nonce_digest, p_nonce_ttl) then
        return jsonb_build_object('ok', false, 'code', 'nonce_replayed');
    end if;

    select * into v_device from license.devices where id = p_device_id;
    if not found
       or v_device.license_id <> p_license_id
       or v_device.revoked_at is not null then
        return jsonb_build_object('ok', false, 'code', 'device_invalid');
    end if;

    select * into v_license from license.licenses where id = p_license_id;
    if not found
       or v_license.revoked_at is not null
       or v_license.expires_at <= v_now then
        return jsonb_build_object('ok', false, 'code', 'license_revoked_or_expired');
    end if;

    select coalesce(jsonb_agg(jsonb_build_object(
               'id', r.id,
               'version', r.version,
               'minimum_launcher_version', r.minimum_launcher_version,
               'signed_manifest', r.signed_manifest
           )), '[]'::jsonb)
      into v_rows
      from license.releases r
     where r.component = p_component
       and r.channel   = v_license.channel
       and r.target    = p_target
       and r.active;

    return jsonb_build_object('ok', true, 'releases', v_rows);
end;
$$;

-- =============================================================================
-- tkauto_claim_download - doi ve lay duong dan file, dung mot lan
-- =============================================================================
create or replace function public.tkauto_claim_download(
    p_artifact_id  text,
    p_device_id    text,
    p_grant_digest text,
    p_grant_ttl    integer,
    p_rate_bucket  text,
    p_rate_limit   integer,
    p_rate_window  integer
) returns jsonb
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now     bigint := extract(epoch from now())::bigint;
    v_device  devices;
    v_license licenses;
    v_release releases;
    v_hit     text;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;

    -- ⛔ KIEM QUYEN TRUOC, DOT VE SAU. Nguoc lai thi mot ve hop le gui kem
    -- artifact_id sai se bi tieu huy ma khach khong tai duoc gi, va ho phai
    -- xin ve moi - tuc mot loi cua minh thanh mot lan tai that bai.
    select * into v_device from license.devices where id = p_device_id;
    if not found or v_device.revoked_at is not null then
        return jsonb_build_object('ok', false, 'code', 'device_invalid');
    end if;

    select * into v_license from license.licenses where id = v_device.license_id;
    if not found
       or v_license.revoked_at is not null
       or v_license.expires_at <= v_now then
        return jsonb_build_object('ok', false, 'code', 'license_revoked_or_expired');
    end if;

    select * into v_release from license.releases where id = p_artifact_id;
    if not found or not v_release.active then
        return jsonb_build_object('ok', false, 'code', 'artifact_unavailable');
    end if;
    if v_release.channel <> v_license.channel then
        return jsonb_build_object('ok', false, 'code', 'artifact_unavailable');
    end if;

    delete from license.used_download_grants where expires_at < v_now;
    insert into license.used_download_grants (digest, expires_at)
    values (p_grant_digest, v_now + p_grant_ttl)
    on conflict (digest) do nothing
    returning digest into v_hit;
    if v_hit is null then
        return jsonb_build_object('ok', false, 'code', 'grant_already_used');
    end if;

    insert into license.audit_events (event_type, actor_id, subject_id, metadata)
    values ('artifact.downloaded', p_device_id, p_artifact_id,
            jsonb_build_object('version', v_release.version));

    return jsonb_build_object(
        'ok', true,
        'storage_object', v_release.storage_object,
        'sha256', v_release.sha256,
        'size_bytes', v_release.size_bytes
    );
end;
$$;

-- =============================================================================
-- quyen goi
-- =============================================================================
revoke all on function public.tkauto_release_check(text, text, text, integer, text, integer, integer, text, text) from public;
revoke all on function public.tkauto_claim_download(text, text, text, integer, text, integer, integer) from public;

do $$
declare
    signature text;
    role_name text;
    signatures text[] := array[
        'public.tkauto_release_check(text, text, text, integer, text, integer, integer, text, text)',
        'public.tkauto_claim_download(text, text, text, integer, text, integer, integer)'
    ];
begin
    foreach signature in array signatures
    loop
        foreach role_name in array array['anon', 'authenticated']
        loop
            if exists (select 1 from pg_roles where rolname = role_name) then
                execute format('revoke all on function %s from %I', signature, role_name);
            end if;
        end loop;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format('grant execute on function %s to service_role', signature);
        end if;
    end loop;
end
$$;

-- ⛔ BO BAN CU 7 THAM SO. `create or replace` khong thay the duoc mot ham co
-- so tham so khac - no tao them mot overload, va luc do Postgres chon ban nao
-- la tuy vao Edge Function goi kieu gi. Hai ban cung ten voi mot ban luon tra
-- "khong co ban phat hanh nao" la dung cai bay da lam app khong khoi dong
-- duoc.
drop function if exists public.tkauto_release_check(text, text, text, integer, text, integer, integer);
