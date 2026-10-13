-- Hai ham kich hoat/gia han. Edge Function lam phan crypto, Postgres lam phan
-- phai nguyen tu.
--
-- ⛔ VI SAO LA RPC CHU KHONG PHAI MAY LAN GOI PostgREST. Ban Python dung
-- `select ... with_for_update()` de khoa hang license truoc khi dem thiet bi,
-- va PostgREST khong co cach nao dien dat `SELECT FOR UPDATE`. Neu Edge
-- Function tu doc roi tu ghi qua REST thi moi buoc la mot transaction rieng:
-- hai may kich hoat cung mot key cung luc deu doc thay "con cho" roi deu ghi,
-- va `max_devices` bi vuot ma khong co loi nao. Nhet ca chuoi vao mot ham
-- plpgsql la cach duy nhat giu lai ngu nghia cua ban Python.
--
-- ⛔ TRA VE PHAN XET DANG jsonb, KHONG raise. `raise` tu plpgsql ve
-- supabase-js thanh mot message chuoi, va luc do Edge Function phai doan ma
-- HTTP tu noi dung chuoi do. Tra ve `{"ok":false,"code":...}` de bang
-- ma->HTTP nam gon mot cho trong TypeScript, doc duoc bang mat.
--
-- ⛔ BIEN KIEU HANG DUOC KHAI KHONG KEM TEN SCHEMA (`v_license licenses;` chu
-- khong `license.licenses`) VA DO LA CO Y. Khong co Postgres tren may phat
-- trien, nen thu duy nhat kiem duoc file nay truoc khi no chay tren du an that
-- la parser cua chinh PostgreSQL qua libpg_query - va parser do khong tra cuu
-- duoc kieu composite co ten schema ("LookupExplicitNamespace only supports
-- pg_catalog and public"), nen mot chu `license.` o cho nay lam
-- `test_supabase_sql_is_parseable.py` bo qua TOAN BO than ham, im lang.
-- Khong mat gi ve do ro rang: moi ham duoi day co `set search_path = license,
-- pg_catalog` ghim cung, nen `licenses` chi co the la `license.licenses`.
-- Dung them `license.` vao day.

-- =============================================================================
-- chong do tan so, dung chung cho ca hai ham
-- -----------------------------------------------------------------------------
-- ⛔ `hits > p_limit` CHU KHONG PHAI `>=`: gia tri sau khi tang la so lan goi
-- da tinh ca lan nay, nen limit=10 phai cho qua lan thu 10. Dung `>=` la am
-- tham chi cho 9.
-- =============================================================================
create or replace function license.claim_rate_slot(
    p_bucket text,
    p_limit integer,
    p_window_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now    bigint := extract(epoch from now())::bigint;
    v_window bigint := (extract(epoch from now())::bigint / p_window_seconds) * p_window_seconds;
    v_hits   integer;
begin
    delete from license.rate_limits
     where window_start < v_now - (p_window_seconds * 4);

    insert into license.rate_limits (bucket, window_start, hits)
    values (p_bucket, v_window, 1)
    on conflict (bucket, window_start)
      do update set hits = license.rate_limits.hits + 1
    returning hits into v_hits;

    return v_hits <= p_limit;
end;
$$;

-- =============================================================================
-- chiem nonce mot lan duy nhat
-- =============================================================================
create or replace function license.claim_nonce(
    p_digest text,
    p_ttl_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now bigint := extract(epoch from now())::bigint;
    v_hit text;
begin
    delete from license.used_nonces where expires_at < v_now;

    insert into license.used_nonces (digest, expires_at)
    values (p_digest, v_now + p_ttl_seconds)
    on conflict (digest) do nothing
    returning digest into v_hit;

    return v_hit is not null;
end;
$$;

-- =============================================================================
-- tkauto_activate
-- =============================================================================
create or replace function public.tkauto_activate(
    p_key_digest     text,
    p_device_id      text,
    p_public_key_pem text,
    p_nonce_digest   text,
    p_nonce_ttl      integer,
    p_rate_bucket    text,
    p_rate_limit     integer,
    p_rate_window    integer
) returns jsonb
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now     bigint := extract(epoch from now())::bigint;
    v_license licenses;
    v_device  devices;
    v_active  integer;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;

    -- ⛔ CHIEM NONCE SAU KHI Edge Function DA KIEM CHU KY, giong thu tu cua ban
    -- Python (verify_device_proof roi moi enforce_fresh_proof). Nguoc lai thi
    -- mot request chu ky rac cung dot duoc nonce cua mot request that.
    if not license.claim_nonce(p_nonce_digest, p_nonce_ttl) then
        return jsonb_build_object('ok', false, 'code', 'nonce_replayed');
    end if;

    select * into v_license
      from license.licenses
     where key_digest = p_key_digest
       for update;

    if not found then
        return jsonb_build_object('ok', false, 'code', 'license_invalid');
    end if;
    if v_license.revoked_at is not null or v_license.expires_at <= v_now then
        return jsonb_build_object('ok', false, 'code', 'license_revoked_or_expired');
    end if;

    select * into v_device
      from license.devices
     where id = p_device_id
       for update;

    if found then
        if v_device.license_id <> v_license.id
           or v_device.public_key_pem <> p_public_key_pem then
            return jsonb_build_object('ok', false, 'code', 'device_conflict');
        end if;
        -- ⛔ CHO NAY KHAC BAN PYTHON MOT CACH CO Y, va la mot lo that o ban do:
        -- `main.py:activate` khong he doc `device.revoked_at`, chi `renew` moi
        -- doc. Tuc thu hoi mot thiet bi chan duoc gia han nhung KHONG chan
        -- kich hoat lai - ma client con giu key nen no goi activate lai la
        -- xong, va thu hoi thiet bi thanh vo nghia. Khong sao chep lo do len
        -- day; ban Python duoc sua kem theo trong cung thay doi nay.
        if v_device.revoked_at is not null then
            return jsonb_build_object('ok', false, 'code', 'device_revoked');
        end if;
        update license.devices
           set last_seen_at = v_now
         where id = p_device_id;
    else
        select count(*) into v_active
          from license.devices
         where license_id = v_license.id and revoked_at is null;

        if v_active >= v_license.max_devices then
            return jsonb_build_object('ok', false, 'code', 'device_limit');
        end if;

        insert into license.devices (id, license_id, public_key_pem,
                                     first_activated_at, last_seen_at)
        values (p_device_id, v_license.id, p_public_key_pem, v_now, v_now);
    end if;

    insert into license.audit_events (event_type, actor_id, subject_id)
    values ('device.activated', p_device_id, v_license.id);

    return jsonb_build_object(
        'ok', true,
        'device_id', p_device_id,
        'license', jsonb_build_object(
            'id', v_license.id,
            'plan', v_license.plan,
            'features', v_license.features,
            'max_accounts', v_license.max_accounts,
            'max_tabs', v_license.max_tabs,
            'channel', v_license.channel,
            'expires_at', v_license.expires_at
        )
    );
end;
$$;

-- =============================================================================
-- tkauto_renew
-- -----------------------------------------------------------------------------
-- Khong nhan key: thiet bi da duoc rang buoc o buoc activate, va chu ky duoc
-- kiem bang khoa cong khai DA LUU chu khong phai khoa gui kem. Nen ham nay
-- phai tra ve `public_key_pem` TRUOC khi Edge Function kiem chu ky - xem
-- `tkauto_renew_device` ben duoi.
-- =============================================================================
create or replace function public.tkauto_renew_device(
    p_device_id  text,
    p_license_id text
) returns jsonb
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_device devices;
begin
    select * into v_device
      from license.devices
     where id = p_device_id;

    if not found
       or v_device.license_id <> p_license_id
       or v_device.revoked_at is not null then
        return jsonb_build_object('ok', false, 'code', 'device_invalid');
    end if;

    return jsonb_build_object(
        'ok', true,
        'public_key_pem', v_device.public_key_pem
    );
end;
$$;

create or replace function public.tkauto_renew(
    p_device_id    text,
    p_license_id   text,
    p_nonce_digest text,
    p_nonce_ttl    integer,
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
    v_license licenses;
    v_device  devices;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    if not license.claim_nonce(p_nonce_digest, p_nonce_ttl) then
        return jsonb_build_object('ok', false, 'code', 'nonce_replayed');
    end if;

    select * into v_device
      from license.devices
     where id = p_device_id
       for update;

    if not found
       or v_device.license_id <> p_license_id
       or v_device.revoked_at is not null then
        return jsonb_build_object('ok', false, 'code', 'device_invalid');
    end if;

    select * into v_license
      from license.licenses
     where id = p_license_id
       for update;

    if not found
       or v_license.revoked_at is not null
       or v_license.expires_at <= v_now then
        return jsonb_build_object('ok', false, 'code', 'license_revoked_or_expired');
    end if;

    update license.devices set last_seen_at = v_now where id = p_device_id;

    insert into license.audit_events (event_type, actor_id, subject_id)
    values ('lease.renewed', p_device_id, v_license.id);

    return jsonb_build_object(
        'ok', true,
        'device_id', p_device_id,
        'license', jsonb_build_object(
            'id', v_license.id,
            'plan', v_license.plan,
            'features', v_license.features,
            'max_accounts', v_license.max_accounts,
            'max_tabs', v_license.max_tabs,
            'channel', v_license.channel,
            'expires_at', v_license.expires_at
        )
    );
end;
$$;

-- =============================================================================
-- quyen goi
-- -----------------------------------------------------------------------------
-- ⛔ GO EXECUTE KHOI anon VA authenticated. Mac dinh Postgres cho PUBLIC goi
-- moi ham moi tao, va `public` LA schema PostgREST phuc vu - nen khong go thi
-- bat ky ai co anon key (tuc bat ky ai co file .exe) goi duoc
-- `/rest/v1/rpc/tkauto_activate` truc tiep, bo qua toan bo phan kiem chu ky
-- thiet bi trong Edge Function. Day la cho de mat nhat trong ca thiet ke.
-- =============================================================================
-- `public` (nghia la PUBLIC pseudo-role) luon ton tai nen go truc tiep duoc;
-- `anon`/`authenticated`/`service_role` thi chi co tren Supabase, xem ghi chu
-- trong file migration truoc.
revoke all on function public.tkauto_activate(text, text, text, text, integer, text, integer, integer) from public;
revoke all on function public.tkauto_renew(text, text, text, integer, text, integer, integer) from public;
revoke all on function public.tkauto_renew_device(text, text) from public;
revoke all on function license.claim_rate_slot(text, integer, integer) from public;
revoke all on function license.claim_nonce(text, integer) from public;

do $$
declare
    signature text;
    role_name text;
    signatures text[] := array[
        'public.tkauto_activate(text, text, text, text, integer, text, integer, integer)',
        'public.tkauto_renew(text, text, text, integer, text, integer, integer)',
        'public.tkauto_renew_device(text, text)',
        'license.claim_rate_slot(text, integer, integer)',
        'license.claim_nonce(text, integer)'
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
    end loop;

    if exists (select 1 from pg_roles where rolname = 'service_role') then
        -- Chi ba ham o `public` duoc cap cho service_role. Hai ham phu
        -- (claim_rate_slot, claim_nonce) nam o schema `license` va chi duoc goi
        -- TU BEN TRONG hai ham tren, nen khong cap gi cho chung.
        execute 'grant execute on function public.tkauto_activate(text, text, text, text, integer, text, integer, integer) to service_role';
        execute 'grant execute on function public.tkauto_renew(text, text, text, integer, text, integer, integer) to service_role';
        execute 'grant execute on function public.tkauto_renew_device(text, text) to service_role';
    end if;
end
$$;
