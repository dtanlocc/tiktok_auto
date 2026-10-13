-- Cho mot thiet bi chuyen sang license MOI khi license cu da chet.
--
-- ⛔ KHONG CO CAI NAY THI "THU HOI ROI PHAT KEY MOI" LA MOT NGO CUT. Do ngay
-- 13/10/2026 tren may that: thu hoi license A, phat key B, kich hoat lai tren
-- CHINH MAY DO -> HTTP 409 "Device identity is already bound differently", va
-- khong co duong nao di tiep. Thiet bi gan vao A vinh vien; khach mua key moi
-- phai doi may. Ban Python co dung loi nay.
--
-- ⛔ VA DAY KHONG PHAI NOI LONG BAO MAT. Mot license da thu hoi hay da het han
-- khong cho quyen gi; chuyen thiet bi khoi no khong lam ai duoc them thu gi.
-- Ba dieu kien duoc giu nguyen:
--   1. khoa cong khai phai khop - device_id = sha256(khoa) nen mot thiet bi
--      khong the mao danh thiet bi khac;
--   2. license CU phai THUC SU da chet - con hieu luc thi van 409, de mot key
--      khong the "cuop" thiet bi cua key khac dang dung;
--   3. phai con o trong max_devices cua license MOI - khong thi mot thiet bi
--      chuyen qua se lot qua phep dem (duong rebind khong di qua nhanh dem o
--      duoi).
--
-- ⛔ VA `revoked_at` DUOC XOA KHI CHUYEN. Thu hoi mot thiet bi la thu hoi no
-- DUOI MOT LICENSE; neu giu lai dau do sang license moi thi mot lan thu hoi
-- bien thanh an vinh vien cho cai may do - mot hinh phat khong ai dinh ra.
-- Trong cung mot license thi `device_revoked` van chan (lo da sua 13/10/2026).

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
    v_old     licenses;
    v_active  integer;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;

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
        -- device_id = sha256(khoa cong khai), nen lech khoa voi cung mot id la
        -- chuyen khong the xay ra that - giu lai nhu mot phep kiem cuoi.
        if v_device.public_key_pem <> p_public_key_pem then
            return jsonb_build_object('ok', false, 'code', 'device_conflict');
        end if;

        if v_device.license_id <> v_license.id then
            select * into v_old
              from license.licenses
             where id = v_device.license_id;

            -- License cu CON SONG -> van tu choi. Khong de mot key cuop thiet
            -- bi cua key khac dang dung.
            if found
               and v_old.revoked_at is null
               and v_old.expires_at > v_now then
                return jsonb_build_object('ok', false, 'code', 'device_conflict');
            end if;

            -- Chuyen sang license moi = mot lan bind moi, nen phai dem.
            select count(*) into v_active
              from license.devices
             where license_id = v_license.id and revoked_at is null;
            if v_active >= v_license.max_devices then
                return jsonb_build_object('ok', false, 'code', 'device_limit');
            end if;

            update license.devices
               set license_id = v_license.id,
                   revoked_at = null,
                   first_activated_at = v_now,
                   last_seen_at = v_now
             where id = p_device_id;

            insert into license.audit_events (event_type, actor_id, subject_id, metadata)
            values ('device.rebound', p_device_id, v_license.id,
                    jsonb_build_object('tu_license', v_device.license_id));
        else
            -- Cung license: thu hoi thiet bi van chan kich hoat lai.
            if v_device.revoked_at is not null then
                return jsonb_build_object('ok', false, 'code', 'device_revoked');
            end if;
            update license.devices
               set last_seen_at = v_now
             where id = p_device_id;
        end if;
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

revoke all on function public.tkauto_activate(text, text, text, text, integer, text, integer, integer) from public;

do $$
declare
    role_name text;
    signature text := 'public.tkauto_activate(text, text, text, text, integer, text, integer, integer)';
begin
    foreach role_name in array array['anon', 'authenticated']
    loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('revoke all on function %s from %I', signature, role_name);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        execute format('grant execute on function %s to service_role', signature);
    end if;
end
$$;
