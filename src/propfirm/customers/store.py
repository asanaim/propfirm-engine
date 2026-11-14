"""Customer records in PostgreSQL (schema `customers` by default).

Identifiers are short and fixed-width: CIF00000001 (customer), ORD00000001
(order), ACC00000001 (trading account), from database sequences.

Schema changes are numbered migrations (`propfirm.pgschema`); a new change
is a new number, never an edit.

Records and caches (CLAUDE.md: records are append-only):
- `order_events` is the record of every order; the status columns of
  `orders` (status, provider_ref, acc, failure, paid_at, provisioned_at) are
  a cache of `history.order_state`, written in the same transaction as each
  event and rebuilt by `rebuild_status`. Triggers refuse UPDATE and DELETE
  on `order_events` and `accounts`, and any change to an order's facts.
- `cif_events` is the record of every customer; `cif.status` is a cache of
  `history.cif_status`. `cif`, `cif_events` and `screenings` hold personal
  data, so they carry no such trigger: the code never updates or deletes
  them, and they stay erasable on a lawful request (data-protection work).

Money is whole cents (BIGINT); discount shares are stored as exact text
("1/10"); times are UTC milliseconds supplied by the caller.
"""

import json
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Self

import asyncpg

from propfirm.customers.history import (
    ORDER_KINDS,
    OrderEvent,
    cif_status,
    order_state,
    placeholder_ref,
)
from propfirm.pgschema import Conn, check_schema
from propfirm.pgschema import migrate as _migrate
from propfirm.policy import stage_of

_LOCK_CLASS = 0x63757374  # "cust"

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE SEQUENCE {s}.cif_seq;
        CREATE SEQUENCE {s}.ord_seq;
        CREATE SEQUENCE {s}.acc_seq;
        CREATE TABLE {s}.cif (
            cif text PRIMARY KEY,
            subject text NOT NULL UNIQUE,
            email text NOT NULL,
            full_name text NOT NULL,
            date_of_birth date NOT NULL,
            residence_country text NOT NULL CHECK (residence_country ~ '^[A-Z]{{2}}$'),
            status text NOT NULL CHECK (status IN ('registered', 'blocked')),
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.cif_events (
            id bigserial PRIMARY KEY,
            cif text REFERENCES {s}.cif (cif),
            subject text NOT NULL,
            ts bigint NOT NULL,
            kind text NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.cif_events (subject, id);
        CREATE TABLE {s}.screenings (
            id bigserial PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            provider text NOT NULL,
            result text NOT NULL CHECK (result IN ('clear', 'hit')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE TABLE {s}.discount_codes (
            code text PRIMARY KEY CHECK (code ~ '^[A-Z0-9-]{{3,32}}$'),
            discount text NOT NULL,
            active boolean NOT NULL,
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.orders (
            ord text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            product_id text NOT NULL,
            size_cents bigint NOT NULL CHECK (size_cents > 0),
            list_price_cents bigint NOT NULL CHECK (list_price_cents > 0),
            discount_kind text NOT NULL,
            discount text NOT NULL,
            discount_code text REFERENCES {s}.discount_codes (code),
            price_cents bigint NOT NULL CHECK (price_cents >= 0),
            rsv_id text NOT NULL,
            status text NOT NULL
                CHECK (status IN ('pending', 'paid', 'provisioned', 'failed')),
            provider text NOT NULL,
            provider_ref text NOT NULL UNIQUE,
            acc text UNIQUE,
            failure text,
            created_at bigint NOT NULL,
            paid_at bigint,
            provisioned_at bigint
        );
        CREATE INDEX ON {s}.orders (cif, product_id);
        CREATE TABLE {s}.accounts (
            acc text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            ord text NOT NULL UNIQUE REFERENCES {s}.orders (ord),
            product_id text NOT NULL,
            rsv_id text NOT NULL,
            size_cents bigint NOT NULL,
            created_at bigint NOT NULL
        );
        CREATE INDEX ON {s}.accounts (cif);
        """,
    ),
    (
        2,
        """
        CREATE TABLE {s}.order_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ord text NOT NULL REFERENCES {s}.orders (ord),
            kind text NOT NULL CHECK (kind IN ('created', 'invoiced', 'paid',
                'payment_repeated', 'account_assigned', 'provision_attempted',
                'provisioned', 'failed')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.order_events (ord, id);
        CREATE FUNCTION {s}.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER order_events_append_only BEFORE UPDATE OR DELETE ON {s}.order_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER accounts_append_only BEFORE UPDATE OR DELETE ON {s}.accounts
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE FUNCTION {s}.order_facts_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'orders are append-only';
            END IF;
            IF (NEW.ord, NEW.cif, NEW.product_id, NEW.size_cents, NEW.list_price_cents,
                NEW.discount_kind, NEW.discount, NEW.discount_code, NEW.price_cents,
                NEW.rsv_id, NEW.provider, NEW.created_at)
               IS DISTINCT FROM
               (OLD.ord, OLD.cif, OLD.product_id, OLD.size_cents, OLD.list_price_cents,
                OLD.discount_kind, OLD.discount, OLD.discount_code, OLD.price_cents,
                OLD.rsv_id, OLD.provider, OLD.created_at) THEN
                RAISE EXCEPTION 'order facts never change';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER orders_facts_fixed BEFORE UPDATE OR DELETE ON {s}.orders
            FOR EACH ROW EXECUTE FUNCTION {s}.order_facts_fixed();

        -- history for orders made before this migration, from their columns
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'created', created_at, jsonb_build_object(
            'product_id', product_id, 'size_cents', size_cents,
            'list_price_cents', list_price_cents, 'discount_kind', discount_kind,
            'discount', discount, 'discount_code', discount_code,
            'price_cents', price_cents, 'rsv_id', rsv_id, 'provider', provider,
            'backfilled', true)
        FROM {s}.orders ORDER BY ord;
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'invoiced', created_at,
            jsonb_build_object('provider_ref', provider_ref, 'backfilled', true)
        FROM {s}.orders WHERE provider_ref <> 'pending-' || ord ORDER BY ord;
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'paid', paid_at, jsonb_build_object('backfilled', true)
        FROM {s}.orders WHERE paid_at IS NOT NULL ORDER BY ord;
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'account_assigned', coalesce(paid_at, created_at),
            jsonb_build_object('acc', acc, 'backfilled', true)
        FROM {s}.orders WHERE acc IS NOT NULL ORDER BY ord;
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'provisioned', provisioned_at,
            jsonb_build_object('acc', acc, 'journal_seq', NULL, 'backfilled', true)
        FROM {s}.orders WHERE status = 'provisioned' ORDER BY ord;
        INSERT INTO {s}.order_events (ord, kind, ts, detail)
        SELECT ord, 'failed', coalesce(paid_at, created_at),
            jsonb_build_object('reason', failure, 'journal_seq', NULL, 'backfilled', true)
        FROM {s}.orders WHERE status = 'failed' ORDER BY ord;
        """,
    ),
    (
        3,
        """
        CREATE SEQUENCE {s}.prg_seq;
        CREATE TABLE {s}.progressions (
            prg text PRIMARY KEY,
            from_acc text NOT NULL UNIQUE REFERENCES {s}.accounts (acc),
            cif text NOT NULL REFERENCES {s}.cif (cif),
            ord text NOT NULL REFERENCES {s}.orders (ord),
            product_id text NOT NULL,
            size_cents bigint NOT NULL CHECK (size_cents > 0),
            from_stage text NOT NULL CHECK (from_stage IN ('s1', 's2')),
            to_stage text CHECK (to_stage IN ('s2', 'funded')),
            passed_seq bigint NOT NULL,
            passed_at bigint NOT NULL,
            created_at bigint NOT NULL,
            status text NOT NULL CHECK (status IN ('provisioning', 'provisioned', 'failed',
                'review', 'approved', 'denied', 'blocked')),
            to_rsv_id text,
            to_acc text,
            failure text,
            decided_by text,
            decided_at bigint
        );
        CREATE INDEX ON {s}.progressions (cif);
        CREATE INDEX ON {s}.progressions (status);
        CREATE TABLE {s}.progression_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            prg text NOT NULL REFERENCES {s}.progressions (prg),
            kind text NOT NULL CHECK (kind IN ('created', 'account_assigned',
                'provision_attempted', 'provisioned', 'failed', 'approved', 'denied')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.progression_events (prg, id);
        CREATE TRIGGER progression_events_append_only
            BEFORE UPDATE OR DELETE ON {s}.progression_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE FUNCTION {s}.progression_facts_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'progressions are append-only';
            END IF;
            IF (NEW.prg, NEW.from_acc, NEW.cif, NEW.ord, NEW.product_id, NEW.size_cents,
                NEW.from_stage, NEW.to_stage, NEW.passed_seq, NEW.passed_at, NEW.created_at)
               IS DISTINCT FROM
               (OLD.prg, OLD.from_acc, OLD.cif, OLD.ord, OLD.product_id, OLD.size_cents,
                OLD.from_stage, OLD.to_stage, OLD.passed_seq, OLD.passed_at, OLD.created_at) THEN
                RAISE EXCEPTION 'progression facts never change';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER progressions_facts_fixed BEFORE UPDATE OR DELETE ON {s}.progressions
            FOR EACH ROW EXECUTE FUNCTION {s}.progression_facts_fixed();
        CREATE TABLE {s}.lifecycle_cursor (
            id smallint PRIMARY KEY CHECK (id = 1),
            seq bigint NOT NULL CHECK (seq >= 0)
        );
        INSERT INTO {s}.lifecycle_cursor (id, seq) VALUES (1, 0);

        -- every stage account of a challenge belongs to the order that bought it
        ALTER TABLE {s}.accounts ADD COLUMN stage text;
        ALTER TABLE {s}.accounts ADD COLUMN progression text REFERENCES {s}.progressions (prg);
        ALTER TABLE {s}.accounts DISABLE TRIGGER accounts_append_only;
        UPDATE {s}.accounts
            SET stage = substring(rsv_id from '-(s1|s2|funded)-v[0-9]+(?:-a[0-9]+)?$');
        ALTER TABLE {s}.accounts ENABLE TRIGGER accounts_append_only;
        ALTER TABLE {s}.accounts ALTER COLUMN stage SET NOT NULL;
        ALTER TABLE {s}.accounts ADD CHECK (stage IN ('s1', 's2', 'funded'));
        ALTER TABLE {s}.accounts DROP CONSTRAINT accounts_ord_key;
        CREATE INDEX ON {s}.accounts (ord);
        """,
    ),
    (
        4,
        """
        CREATE SEQUENCE {s}.onb_seq;
        CREATE SEQUENCE {s}.kyc_seq;
        CREATE SEQUENCE {s}.ctr_seq;
        -- identity checks: personal data, so no trigger (erasable on a lawful
        -- request); the code only ever inserts
        CREATE TABLE {s}.kyc_cases (
            kyc text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            provider text NOT NULL,
            session_id text NOT NULL,
            url text NOT NULL,
            created_at bigint NOT NULL,
            UNIQUE (provider, session_id)
        );
        CREATE INDEX ON {s}.kyc_cases (cif);
        CREATE TABLE {s}.kyc_results (
            kyc text PRIMARY KEY REFERENCES {s}.kyc_cases (kyc),
            result text NOT NULL CHECK (result IN ('approved', 'declined')),
            full_name text,
            date_of_birth date,
            document_country text CHECK (document_country ~ '^[A-Z]{{2}}$'),
            ts bigint NOT NULL,
            detail jsonb NOT NULL,
            CHECK (result = 'declined' OR full_name IS NOT NULL)
        );
        CREATE TABLE {s}.onboardings (
            onb text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            source text NOT NULL CHECK (source IN ('progression', 'order')),
            prg text UNIQUE REFERENCES {s}.progressions (prg),
            ord text NOT NULL REFERENCES {s}.orders (ord),
            product_id text NOT NULL,
            size_cents bigint NOT NULL CHECK (size_cents > 0),
            rsv_id text NOT NULL,
            created_at bigint NOT NULL,
            status text NOT NULL CHECK (status IN ('verification', 'verification_declined',
                'checks', 'review', 'rejected', 'contract', 'signature', 'signed',
                'provisioned', 'failed')),
            kyc text REFERENCES {s}.kyc_cases (kyc),
            kyc_attempts integer NOT NULL DEFAULT 0,
            acc text,
            contract text,
            failure text,
            CHECK ((source = 'progression') = (prg IS NOT NULL))
        );
        CREATE UNIQUE INDEX ON {s}.onboardings (ord) WHERE source = 'order';
        CREATE INDEX ON {s}.onboardings (cif);
        CREATE INDEX ON {s}.onboardings (status);
        CREATE TABLE {s}.onboarding_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            onb text NOT NULL REFERENCES {s}.onboardings (onb),
            kind text NOT NULL CHECK (kind IN ('created', 'kyc_requested', 'kyc_declined',
                'kyc_approved', 'screened', 'review_required', 'approved', 'denied',
                'rejected', 'cleared', 'account_assigned', 'contract_issued', 'signed',
                'provision_attempted', 'provisioned', 'failed')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.onboarding_events (onb, id);
        CREATE TRIGGER onboarding_events_append_only
            BEFORE UPDATE OR DELETE ON {s}.onboarding_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE FUNCTION {s}.onboarding_facts_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'onboardings are append-only';
            END IF;
            IF (NEW.onb, NEW.cif, NEW.source, NEW.prg, NEW.ord, NEW.product_id,
                NEW.size_cents, NEW.rsv_id, NEW.created_at)
               IS DISTINCT FROM
               (OLD.onb, OLD.cif, OLD.source, OLD.prg, OLD.ord, OLD.product_id,
                OLD.size_cents, OLD.rsv_id, OLD.created_at) THEN
                RAISE EXCEPTION 'onboarding facts never change';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER onboardings_facts_fixed BEFORE UPDATE OR DELETE ON {s}.onboardings
            FOR EACH ROW EXECUTE FUNCTION {s}.onboarding_facts_fixed();
        -- signed agreements are legal records: kept whole
        CREATE TABLE {s}.contracts (
            contract text PRIMARY KEY,
            onb text NOT NULL REFERENCES {s}.onboardings (onb),
            template text NOT NULL,
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            body text NOT NULL,
            draft boolean NOT NULL,
            created_at bigint NOT NULL
        );
        CREATE TRIGGER contracts_append_only BEFORE UPDATE OR DELETE ON {s}.contracts
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        ALTER TABLE {s}.accounts ADD COLUMN onboarding text REFERENCES {s}.onboardings (onb);
        """,
    ),
    (
        5,
        """
        CREATE SEQUENCE {s}.cas_seq;
        CREATE SEQUENCE {s}.alr_seq;
        CREATE TABLE {s}.cases (
            cas text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            opened_at bigint NOT NULL,
            status text NOT NULL CHECK (status IN ('open', 'decision_pending', 'actioning',
                'closed')),
            severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
            due_at bigint NOT NULL,
            assignee text,
            outcome text CHECK (outcome IN ('cleared', 'warning', 'actioned')),
            proposed_by text,
            closed_at bigint
        );
        -- one case not closed per customer
        CREATE UNIQUE INDEX ON {s}.cases (cif) WHERE status <> 'closed';
        CREATE INDEX ON {s}.cases (status, due_at);
        CREATE TABLE {s}.case_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cas text NOT NULL REFERENCES {s}.cases (cas),
            kind text NOT NULL CHECK (kind IN ('opened', 'alert', 'assigned', 'note',
                'closed', 'proposed', 'rejected', 'confirmed', 'applied', 'reopened')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.case_events (cas, id);
        CREATE TRIGGER case_events_append_only BEFORE UPDATE OR DELETE ON {s}.case_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TABLE {s}.alerts (
            alr text PRIMARY KEY,
            cas text NOT NULL REFERENCES {s}.cases (cas),
            cif text NOT NULL REFERENCES {s}.cif (cif),
            acc text,
            source text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('integrity', 'fraud', 'aml')),
            severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.alerts (cif);
        CREATE TRIGGER alerts_append_only BEFORE UPDATE OR DELETE ON {s}.alerts
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE FUNCTION {s}.case_facts_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'cases are append-only';
            END IF;
            IF (NEW.cas, NEW.cif, NEW.opened_at) IS DISTINCT FROM
               (OLD.cas, OLD.cif, OLD.opened_at) THEN
                RAISE EXCEPTION 'case facts never change';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER cases_facts_fixed BEFORE UPDATE OR DELETE ON {s}.cases
            FOR EACH ROW EXECUTE FUNCTION {s}.case_facts_fixed();
        """,
    ),
    (
        6,
        """
        CREATE SEQUENCE {s}.wal_seq;
        CREATE SEQUENCE {s}.po_seq;
        -- payout wallets: the latest per customer is in use; kept for the
        -- payout record (append-only)
        CREATE TABLE {s}.wallets (
            wal text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            network text NOT NULL CHECK (network = 'TRC20'),
            address text NOT NULL CHECK (address ~ '^T[1-9A-HJ-NP-Za-km-z]{{33}}$'),
            added_at bigint NOT NULL,
            added_by text NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.wallets (cif, added_at);
        CREATE TRIGGER wallets_append_only BEFORE UPDATE OR DELETE ON {s}.wallets
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TABLE {s}.payouts (
            po text PRIMARY KEY,
            acc text NOT NULL REFERENCES {s}.accounts (acc),
            cif text NOT NULL REFERENCES {s}.cif (cif),
            ord text NOT NULL REFERENCES {s}.orders (ord),
            wal text NOT NULL REFERENCES {s}.wallets (wal),
            requested_at bigint NOT NULL,
            trader_amount_cents bigint NOT NULL CHECK (trader_amount_cents > 0),
            debit_cents bigint NOT NULL CHECK (debit_cents > 0),
            status text NOT NULL CHECK (status IN ('requested', 'approved', 'debited', 'sent',
                'paid', 'denied', 'cancelled', 'failed')),
            approvals integer NOT NULL DEFAULT 0,
            debited_at bigint,
            paid_at bigint,
            provider_ref text,
            failure text
        );
        -- one payout in progress per account
        CREATE UNIQUE INDEX ON {s}.payouts (acc) WHERE status IN ('requested', 'approved');
        CREATE INDEX ON {s}.payouts (status);
        CREATE INDEX ON {s}.payouts (ord);
        CREATE TABLE {s}.payout_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            po text NOT NULL REFERENCES {s}.payouts (po),
            kind text NOT NULL CHECK (kind IN ('requested', 'approved', 'denied', 'cancelled',
                'rechecked', 'failed', 'debit_attempted', 'debited', 'sent', 'send_failed',
                'paid')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.payout_events (po, id);
        CREATE TRIGGER payout_events_append_only BEFORE UPDATE OR DELETE ON {s}.payout_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE FUNCTION {s}.payout_facts_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'payouts are append-only';
            END IF;
            IF (NEW.po, NEW.acc, NEW.cif, NEW.ord, NEW.wal, NEW.requested_at,
                NEW.trader_amount_cents, NEW.debit_cents)
               IS DISTINCT FROM
               (OLD.po, OLD.acc, OLD.cif, OLD.ord, OLD.wal, OLD.requested_at,
                OLD.trader_amount_cents, OLD.debit_cents) THEN
                RAISE EXCEPTION 'payout facts never change';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER payouts_facts_fixed BEFORE UPDATE OR DELETE ON {s}.payouts
            FOR EACH ROW EXECUTE FUNCTION {s}.payout_facts_fixed();
        """,
    ),
    (
        7,
        """
        CREATE SEQUENCE {s}.ntf_seq;
        -- security emails (outbox); personal data: insert-only by the code,
        -- erasable
        CREATE TABLE {s}.notifications (
            ntf text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            kind text NOT NULL CHECK (kind IN ('wallet_changed')),
            recipient text NOT NULL,
            subject text NOT NULL,
            body text NOT NULL,
            created_at bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.notifications (cif);
        CREATE TABLE {s}.notification_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ntf text NOT NULL REFERENCES {s}.notifications (ntf) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('sent', 'failed')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.notification_events (ntf, id);
        CREATE TRIGGER notification_events_append_only BEFORE UPDATE ON {s}.notification_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        8,
        """
        -- how far the news-violation sweep has read the engine journal
        CREATE TABLE {s}.news_cursor (
            id int PRIMARY KEY CHECK (id = 1),
            seq bigint NOT NULL
        );
        INSERT INTO {s}.news_cursor (id, seq) VALUES (1, 0);
        """,
    ),
    (
        9,
        """
        -- rule AC00 (8 Nov 2026): a review decision needs two people; the first
        -- is a proposal, a second person carries it out or rejects it
        ALTER TABLE {s}.progression_events DROP CONSTRAINT progression_events_kind_check;
        ALTER TABLE {s}.progression_events ADD CONSTRAINT progression_events_kind_check
            CHECK (kind IN ('created', 'account_assigned', 'provision_attempted',
                'provisioned', 'failed', 'approved', 'denied', 'proposed',
                'proposal_rejected'));
        ALTER TABLE {s}.onboarding_events DROP CONSTRAINT onboarding_events_kind_check;
        ALTER TABLE {s}.onboarding_events ADD CONSTRAINT onboarding_events_kind_check
            CHECK (kind IN ('created', 'kyc_requested', 'kyc_declined', 'kyc_approved',
                'screened', 'review_required', 'approved', 'denied', 'proposed',
                'proposal_rejected', 'rejected', 'cleared', 'account_assigned',
                'contract_issued', 'signed', 'provision_attempted', 'provisioned', 'failed'));
        """,
    ),
    (
        10,
        """
        -- market break rule (D-13.23, 13 Nov 2026): the trader is emailed when
        -- trades are closed before a market break
        ALTER TABLE {s}.notifications DROP CONSTRAINT notifications_kind_check;
        ALTER TABLE {s}.notifications ADD CONSTRAINT notifications_kind_check
            CHECK (kind IN ('wallet_changed', 'market_break_close'));
        -- how far the market-break sweep has read the engine journal
        CREATE TABLE {s}.break_cursor (
            id int PRIMARY KEY CHECK (id = 1),
            seq bigint NOT NULL
        );
        INSERT INTO {s}.break_cursor (id, seq) VALUES (1, 0);
        """,
    ),
    (
        11,
        """
        -- owner decision, 14 Nov 2026 (D-14.1): automatic emails only for
        -- serious matters; a market-break closure is not one, so its sweep is
        -- removed. The notice kind stays allowed for any row already written.
        DROP TABLE {s}.break_cursor;
        """,
    ),
    (
        12,
        """
        -- D-14.1 as amended 14 Nov 2026: an evaluation pass is emailed
        ALTER TABLE {s}.notifications DROP CONSTRAINT notifications_kind_check;
        ALTER TABLE {s}.notifications ADD CONSTRAINT notifications_kind_check
            CHECK (kind IN ('wallet_changed', 'market_break_close', 'stage_passed'));
        """,
    ),
    (
        13,
        """
        -- the public leaderboard (14 Nov 2026): a trader who opts out is
        -- left off it; one row per customer, removed when they opt back in
        CREATE TABLE {s}.leaderboard_hidden (
            cif text PRIMARY KEY REFERENCES {s}.cif (cif),
            hidden_at bigint NOT NULL
        );
        """,
    ),
    (
        14,
        """
        -- bad-price corrections (D-14.4): one person proposes, another
        -- approves; the engine inputs then reverse each account's effect
        CREATE SEQUENCE {s}.cor_seq;
        CREATE TABLE {s}.corrections (
            cor text PRIMARY KEY,
            symbol text NOT NULL,
            price_seq bigint NOT NULL,
            bad_price bigint NOT NULL,
            good_price bigint NOT NULL,
            price_ts bigint NOT NULL,
            reason text NOT NULL,
            items jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('proposed', 'applied', 'rejected')),
            proposed_by text NOT NULL,
            proposed_at bigint NOT NULL,
            decided_by text,
            decided_at bigint,
            outcome jsonb
        );
        CREATE UNIQUE INDEX corrections_one_open ON {s}.corrections (symbol, price_seq)
            WHERE status <> 'rejected';
        """,
    ),
    (
        15,
        """
        -- trader classes, first use (D-14.5): an account flagged for short
        -- holds is class C; staff set it back; every change kept
        CREATE TABLE {s}.account_class_changes (
            acc text NOT NULL REFERENCES {s}.accounts (acc),
            at bigint NOT NULL,
            class text NOT NULL CHECK (class IN ('A', 'B', 'C', 'D')),
            reason text NOT NULL,
            set_by text NOT NULL,
            PRIMARY KEY (acc, at)
        );
        """,
    ),
    (
        16,
        """
        -- scaling steps (D-14.8): the daily job proposes, staff approve, the
        -- engine adds the allocation; one open or applied step per number
        CREATE SEQUENCE {s}.scl_seq;
        CREATE TABLE {s}.scaling_steps (
            scl text PRIMARY KEY,
            acc text NOT NULL REFERENCES {s}.accounts (acc),
            ord text NOT NULL REFERENCES {s}.orders (ord),
            step integer NOT NULL CHECK (step >= 1),
            add_cents bigint NOT NULL CHECK (add_cents > 0),
            measures jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('proposed', 'applied', 'rejected')),
            proposed_at bigint NOT NULL,
            decided_by text,
            decided_at bigint,
            balance_after_cents bigint,
            outcome jsonb
        );
        CREATE UNIQUE INDEX scaling_one_per_step ON {s}.scaling_steps (acc, step)
            WHERE status <> 'rejected';
        """,
    ),
    (
        17,
        """
        -- retakes (D-13.6): the order bought at the retake price and the
        -- breached order it uses; a failed or lapsed order frees the breach
        CREATE TABLE {s}.retakes (
            ord text PRIMARY KEY REFERENCES {s}.orders (ord),
            of_ord text NOT NULL REFERENCES {s}.orders (ord),
            created_at bigint NOT NULL
        );
        CREATE INDEX ON {s}.retakes (of_ord);
        """,
    ),
)


class RetakeTakenError(Exception):
    """The breach's one retake is already used by another order."""


class AlreadyRegisteredError(Exception):
    """This login identity already has a CIF."""


@dataclass(frozen=True, slots=True)
class CifRecord:
    cif: str
    subject: str
    email: str
    full_name: str
    date_of_birth: date
    residence_country: str
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class OrderRecord:
    ord: str
    cif: str
    product_id: str
    size_cents: int
    list_price_cents: int
    discount_kind: str
    discount: Fraction
    discount_code: str | None
    price_cents: int
    rsv_id: str
    status: str
    provider: str
    provider_ref: str
    acc: str | None
    failure: str | None
    created_at: int
    paid_at: int | None
    provisioned_at: int | None


@dataclass(frozen=True, slots=True)
class AccountRecord:
    acc: str
    cif: str
    ord: str
    product_id: str
    rsv_id: str
    size_cents: int
    created_at: int
    stage: str  # s1, s2 or funded
    progression: str | None = None  # the PRG that opened it; None: bought directly
    onboarding: str | None = None  # the ONB that opened it (funded after a progression)


def _check(name: str) -> str:
    return check_schema(name)


async def migrate(conn: Conn, schema: str) -> list[int]:
    """Apply the customer migrations not yet applied; return their numbers."""
    return await _migrate(conn, schema, _MIGRATIONS, _LOCK_CLASS)


def _share(text: str) -> Fraction:
    return Fraction(text)


def _order(row: asyncpg.Record) -> OrderRecord:
    return OrderRecord(
        ord=row["ord"],
        cif=row["cif"],
        product_id=row["product_id"],
        size_cents=row["size_cents"],
        list_price_cents=row["list_price_cents"],
        discount_kind=row["discount_kind"],
        discount=_share(row["discount"]),
        discount_code=row["discount_code"],
        price_cents=row["price_cents"],
        rsv_id=row["rsv_id"],
        status=row["status"],
        provider=row["provider"],
        provider_ref=row["provider_ref"],
        acc=row["acc"],
        failure=row["failure"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
        provisioned_at=row["provisioned_at"],
    )


def _cif(row: asyncpg.Record) -> CifRecord:
    return CifRecord(
        cif=row["cif"],
        subject=row["subject"],
        email=row["email"],
        full_name=row["full_name"],
        date_of_birth=row["date_of_birth"],
        residence_country=row["residence_country"],
        status=row["status"],
        created_at=row["created_at"],
    )


class CustomerStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = _check(schema)

    @classmethod
    async def open(cls, pool: asyncpg.Pool, schema: str) -> Self:
        async with pool.acquire() as conn:
            await migrate(conn, schema)
        return cls(pool, schema)

    # --- CIF ------------------------------------------------------------------------

    async def register(
        self,
        subject: str,
        email: str,
        full_name: str,
        date_of_birth: date,
        residence_country: str,
        ts: int,
        detail: dict[str, Any],
    ) -> CifRecord:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(f"SELECT 1 FROM {s}.cif WHERE subject = $1", subject):
                raise AlreadyRegisteredError(subject)
            number = await conn.fetchval(f"SELECT nextval('{s}.cif_seq')")
            cif = f"CIF{number:08d}"
            try:
                row = await conn.fetchrow(
                    f"INSERT INTO {s}.cif (cif, subject, email, full_name, date_of_birth, "
                    f"residence_country, status, created_at) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, 'registered', $7) RETURNING *",
                    cif,
                    subject,
                    email,
                    full_name,
                    date_of_birth,
                    residence_country,
                    ts,
                )
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyRegisteredError(subject) from exc
            await self._event(conn, cif, subject, ts, "registered", detail)
            assert row is not None
            return _cif(row)

    async def refused(self, subject: str, ts: int, detail: dict[str, Any]) -> None:
        """Record a refused registration (no CIF is created)."""
        async with self._pool.acquire() as conn:
            await self._event(conn, None, subject, ts, "registration_refused", detail)

    async def cif(self, cif: str) -> CifRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.cif WHERE cif = $1", cif)
        return None if row is None else _cif(row)

    async def cif_by_subject(self, subject: str) -> CifRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.cif WHERE subject = $1", subject)
        return None if row is None else _cif(row)

    async def block(self, cif: str, subject: str, ts: int, detail: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SELECT 1 FROM {self._s}.cif WHERE cif = $1 FOR UPDATE", cif)
            await self._event(conn, cif, subject, ts, "blocked", detail)
            await self._cache_cif(conn, cif)

    async def _cache_cif(self, conn: Conn, cif: str) -> bool:
        """Write `cif.status` from the customer's events; True if it changed."""
        s = self._s
        kinds = [
            r["kind"]
            for r in await conn.fetch(
                f"SELECT kind FROM {s}.cif_events WHERE cif = $1 ORDER BY id", cif
            )
        ]
        result: str = await conn.execute(
            f"UPDATE {s}.cif SET status = $2 WHERE cif = $1 AND status <> $2",
            cif,
            cif_status(cif, kinds),
        )
        return result.endswith(" 1")

    async def events(self, subject: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT cif, ts, kind, detail::text AS detail FROM {self._s}.cif_events "
            f"WHERE subject = $1 ORDER BY id",
            subject,
        )
        return [
            {"cif": r["cif"], "ts": r["ts"], "kind": r["kind"], "detail": json.loads(r["detail"])}
            for r in rows
        ]

    async def _event(
        self,
        conn: Conn,
        cif: str | None,
        subject: str,
        ts: int,
        kind: str,
        detail: dict[str, Any],
    ) -> None:
        await conn.execute(
            f"INSERT INTO {self._s}.cif_events (cif, subject, ts, kind, detail) "
            f"VALUES ($1, $2, $3, $4, $5::jsonb)",
            cif,
            subject,
            ts,
            kind,
            json.dumps(detail, sort_keys=True),
        )

    # --- screening ------------------------------------------------------------------

    async def record_screening(
        self, cif: str, provider: str, result: str, ts: int, detail: dict[str, Any]
    ) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.screenings (cif, provider, result, ts, detail) "
            f"VALUES ($1, $2, $3, $4, $5::jsonb)",
            cif,
            provider,
            result,
            ts,
            json.dumps(detail, sort_keys=True),
        )

    async def screened_clear(self, cif: str) -> bool:
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.screenings WHERE cif = $1 AND result = 'clear'", cif
        )
        return found is not None

    # --- discount codes -------------------------------------------------------------

    async def add_code(self, code: str, discount: Fraction, ts: int) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.discount_codes (code, discount, active, created_at) "
            f"VALUES ($1, $2, true, $3)",
            code,
            str(discount),
            ts,
        )

    async def code_discount(self, code: str) -> Fraction | None:
        text = await self._pool.fetchval(
            f"SELECT discount FROM {self._s}.discount_codes WHERE code = $1 AND active", code
        )
        return None if text is None else _share(text)

    # --- orders and accounts --------------------------------------------------------

    async def prior_accounts(self, cif: str, product_id: str) -> int:
        count: int = await self._pool.fetchval(
            f"SELECT count(*) FROM {self._s}.orders WHERE cif = $1 AND product_id = $2 "
            f"AND status IN ('paid', 'provisioned')",
            cif,
            product_id,
        )
        return count

    async def retakes(self, cif: str, hold_since: int) -> dict[str, str]:
        """The customer's live retake orders: ord -> the breached ord it uses
        (paid, or pending since `hold_since`)."""
        rows = await self._pool.fetch(
            f"SELECT r.ord, r.of_ord FROM {self._s}.retakes r "
            f"JOIN {self._s}.orders o ON o.ord = r.ord WHERE o.cif = $1 AND "
            f"(o.status IN ('paid', 'provisioned') "
            f"OR (o.status = 'pending' AND o.created_at >= $2))",
            cif,
            hold_since,
        )
        return {r["ord"]: r["of_ord"] for r in rows}

    async def create_order(
        self,
        cif: str,
        product_id: str,
        size_cents: int,
        list_price_cents: int,
        discount_kind: str,
        discount: Fraction,
        discount_code: str | None,
        price_cents: int,
        rsv_id: str,
        provider: str,
        ts: int,
        retake_of: str | None = None,
        hold_since: int = 0,
        addon_cents: int = 0,
    ) -> OrderRecord:
        """With `retake_of`, the order uses that breached order's retake;
        refused (RetakeTakenError) if another order placed since `hold_since`
        or paid already uses it. The breached order's row is locked, so two
        orders cannot take one retake."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            if retake_of is not None:
                await conn.execute(f"SELECT 1 FROM {s}.orders WHERE ord = $1 FOR UPDATE", retake_of)
                taken = await conn.fetchval(
                    f"SELECT r.ord FROM {s}.retakes r JOIN {s}.orders o ON o.ord = r.ord "
                    f"WHERE r.of_ord = $1 AND (o.status IN ('paid', 'provisioned') "
                    f"OR (o.status = 'pending' AND o.created_at >= $2))",
                    retake_of,
                    hold_since,
                )
                if taken is not None:
                    raise RetakeTakenError(retake_of)
            number = await conn.fetchval(f"SELECT nextval('{s}.ord_seq')")
            ord_id = f"ORD{number:08d}"
            await conn.execute(
                f"INSERT INTO {s}.orders (ord, cif, product_id, size_cents, list_price_cents, "
                f"discount_kind, discount, discount_code, price_cents, rsv_id, status, provider, "
                f"provider_ref, created_at) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending', $11, $12, $13)",
                ord_id,
                cif,
                product_id,
                size_cents,
                list_price_cents,
                discount_kind,
                str(discount),
                discount_code,
                price_cents,
                rsv_id,
                provider,
                placeholder_ref(ord_id),
                ts,
            )
            facts = {
                "product_id": product_id,
                "size_cents": size_cents,
                "list_price_cents": list_price_cents,
                "discount_kind": discount_kind,
                "discount": str(discount),
                "discount_code": discount_code,
                "price_cents": price_cents,
                "rsv_id": rsv_id,
                "provider": provider,
            }
            if addon_cents:
                facts["addon_cents"] = addon_cents  # the add-ons are in rsv_id
            if retake_of is not None:
                facts["retake_of"] = retake_of
                await conn.execute(
                    f"INSERT INTO {s}.retakes (ord, of_ord, created_at) VALUES ($1, $2, $3)",
                    ord_id,
                    retake_of,
                    ts,
                )
            return await self._append(conn, ord_id, "created", ts, facts)

    async def set_provider_ref(self, ord_id: str, provider_ref: str, ts: int) -> OrderRecord:
        """The invoice exists: record the provider's reference."""
        async with self._pool.acquire() as conn, conn.transaction():
            await self._locked(conn, ord_id)
            return await self._append(conn, ord_id, "invoiced", ts, {"provider_ref": provider_ref})

    async def order(self, ord_id: str) -> OrderRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.orders WHERE ord = $1", ord_id)
        return None if row is None else _order(row)

    async def order_by_ref(self, provider: str, provider_ref: str) -> OrderRecord | None:
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.orders WHERE provider = $1 AND provider_ref = $2",
            provider,
            provider_ref,
        )
        return None if row is None else _order(row)

    async def orders_for(self, cif: str) -> list[OrderRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.orders WHERE cif = $1 ORDER BY ord", cif
        )
        return [_order(r) for r in rows]

    async def order_history(self, ord_id: str) -> list[OrderEvent]:
        """The order's events, oldest first: the record its status comes from."""
        async with self._pool.acquire() as conn:
            return await self._events(conn, ord_id)

    async def mark_paid(self, ord_id: str, ts: int) -> bool:
        """pending -> paid; False when it was not pending (a repeated notice,
        recorded as payment_repeated)."""
        async with self._pool.acquire() as conn, conn.transaction():
            order = await self._locked(conn, ord_id)
            first = order.status == "pending"
            kind = "paid" if first else "payment_repeated"
            await self._append(conn, ord_id, kind, ts, {"provider_ref": order.provider_ref})
            return first

    async def assign_account(self, ord_id: str, ts: int) -> str:
        """The order's ACC id, allocated once and kept, so a retried
        provisioning opens the same account."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            order = await self._locked(conn, ord_id)
            if order.acc is not None:
                return order.acc
            number = await conn.fetchval(f"SELECT nextval('{s}.acc_seq')")
            acc = f"ACC{number:08d}"
            await self._append(conn, ord_id, "account_assigned", ts, {"acc": acc})
            return acc

    async def provision_attempted(self, ord_id: str, acc: str, entry_id: str, ts: int) -> None:
        """OpenAccount was sent to the engine as stream entry `entry_id`."""
        async with self._pool.acquire() as conn, conn.transaction():
            await self._locked(conn, ord_id)
            await self._append(
                conn, ord_id, "provision_attempted", ts, {"acc": acc, "entry_id": entry_id}
            )

    async def mark_provisioned(
        self, order: OrderRecord, acc: str, ts: int, journal_seq: int | None
    ) -> None:
        """paid -> provisioned, with the account's owner record; nothing
        when the order is no longer paid."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            current = await self._locked(conn, order.ord)
            if current.status != "paid":
                return
            await conn.execute(
                f"INSERT INTO {s}.accounts (acc, cif, ord, product_id, rsv_id, size_cents, "
                f"created_at, stage) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                acc,
                current.cif,
                current.ord,
                current.product_id,
                current.rsv_id,
                current.size_cents,
                ts,
                stage_of(current.rsv_id).value,
            )
            await self._append(
                conn, order.ord, "provisioned", ts, {"acc": acc, "journal_seq": journal_seq}
            )

    async def mark_failed(
        self, ord_id: str, failure: str, ts: int, journal_seq: int | None
    ) -> None:
        """paid -> failed; nothing when the order is no longer paid."""
        async with self._pool.acquire() as conn, conn.transaction():
            if (await self._locked(conn, ord_id)).status != "paid":
                return
            await self._append(
                conn, ord_id, "failed", ts, {"reason": failure, "journal_seq": journal_seq}
            )

    async def rebuild_status(self) -> list[str]:
        """Rewrite every cached status from the records; return the ORD and
        CIF ids whose cache was wrong (none, unless someone edited it)."""
        s = self._s
        fixed: list[str] = []
        ords = [
            r["ord"] for r in await self._pool.fetch(f"SELECT ord FROM {s}.orders ORDER BY ord")
        ]
        for ord_id in ords:
            async with self._pool.acquire() as conn, conn.transaction():
                before = await self._locked(conn, ord_id)
                if await self._write_cache(conn, ord_id) != before:
                    fixed.append(ord_id)
        cifs = [r["cif"] for r in await self._pool.fetch(f"SELECT cif FROM {s}.cif ORDER BY cif")]
        for cif in cifs:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SELECT 1 FROM {s}.cif WHERE cif = $1 FOR UPDATE", cif)
                if await self._cache_cif(conn, cif):
                    fixed.append(cif)
        return fixed

    async def _locked(self, conn: Conn, ord_id: str) -> OrderRecord:
        row = await conn.fetchrow(
            f"SELECT * FROM {self._s}.orders WHERE ord = $1 FOR UPDATE", ord_id
        )
        if row is None:
            raise KeyError(f"unknown order {ord_id!r}")
        return _order(row)

    async def _events(self, conn: Conn, ord_id: str) -> list[OrderEvent]:
        rows = await conn.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.order_events "
            f"WHERE ord = $1 ORDER BY id",
            ord_id,
        )
        return [OrderEvent(ord_id, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def _append(
        self, conn: Conn, ord_id: str, kind: str, ts: int, detail: dict[str, Any]
    ) -> OrderRecord:
        """Record an event, then write the cache from the whole history (one
        transaction): the cache can only ever be what the record says."""
        assert kind in ORDER_KINDS
        await conn.execute(
            f"INSERT INTO {self._s}.order_events (ord, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            ord_id,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        return await self._write_cache(conn, ord_id)

    async def _write_cache(self, conn: Conn, ord_id: str) -> OrderRecord:
        state = order_state(ord_id, await self._events(conn, ord_id))
        row = await conn.fetchrow(
            f"UPDATE {self._s}.orders SET status = $2, provider_ref = $3, acc = $4, "
            f"failure = $5, paid_at = $6, provisioned_at = $7 WHERE ord = $1 RETURNING *",
            ord_id,
            state.status,
            state.provider_ref,
            state.acc,
            state.failure,
            state.paid_at,
            state.provisioned_at,
        )
        assert row is not None
        return _order(row)

    async def unprovisioned(self) -> list[OrderRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.orders WHERE status = 'paid' ORDER BY ord"
        )
        return [_order(r) for r in rows]

    async def ended_orders(self, cif: str) -> set[str]:
        """The trader's orders whose passed account will not go on: its
        progression was denied or blocked (the account holds no
        capital any more)."""
        rows = await self._pool.fetch(
            f"SELECT DISTINCT ord FROM {self._s}.progressions "
            "WHERE cif = $1 AND status IN ('denied', 'blocked')",
            cif,
        )
        return {r["ord"] for r in rows}

    async def account_class(self, acc: str) -> tuple[str, str, int] | None:
        """The account's class now (class, reason, since), or None: unclassed."""
        row = await self._pool.fetchrow(
            f"SELECT class, reason, at FROM {self._s}.account_class_changes WHERE acc = $1 "
            f"ORDER BY at DESC LIMIT 1",
            acc,
        )
        return None if row is None else (row["class"], row["reason"], row["at"])

    async def set_account_class(self, acc: str, cls: str, reason: str, by: str, ts: int) -> None:
        """Record a class change (kept: the latest is in force)."""
        if cls not in ("A", "B", "C", "D"):
            raise ValueError(f"no class {cls!r}")
        await self._pool.execute(
            f"INSERT INTO {self._s}.account_class_changes (acc, at, class, reason, set_by) "
            f"VALUES ($1, $2, $3, $4, $5) ON CONFLICT (acc, at) DO NOTHING",
            acc,
            ts,
            cls,
            reason,
            by,
        )

    async def leaderboard_entrants(self) -> list[tuple[AccountRecord, str, str]]:
        """Funded accounts of customers who have not opted out of the
        leaderboard, each with the owner's full name and country."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT a.*, c.full_name AS owner_name, c.residence_country AS owner_country "
            f"FROM {s}.accounts a JOIN {s}.cif c USING (cif) WHERE a.stage = 'funded' "
            f"AND NOT EXISTS (SELECT 1 FROM {s}.leaderboard_hidden h WHERE h.cif = a.cif) "
            f"ORDER BY a.acc"
        )
        out = []
        for r in rows:
            fields = dict(r)
            name, country = fields.pop("owner_name"), fields.pop("owner_country")
            out.append((AccountRecord(**fields), name, country))
        return out

    async def leaderboard_visible(self, cif: str) -> bool:
        hidden = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.leaderboard_hidden WHERE cif = $1", cif
        )
        return hidden is None

    async def set_leaderboard_visible(self, cif: str, visible: bool, ts: int) -> None:
        if visible:
            await self._pool.execute(
                f"DELETE FROM {self._s}.leaderboard_hidden WHERE cif = $1", cif
            )
        else:
            await self._pool.execute(
                f"INSERT INTO {self._s}.leaderboard_hidden (cif, hidden_at) VALUES ($1, $2) "
                f"ON CONFLICT (cif) DO NOTHING",
                cif,
                ts,
            )

    async def accounts_for(self, cif: str) -> list[AccountRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.accounts WHERE cif = $1 ORDER BY acc", cif
        )
        return [AccountRecord(**dict(r)) for r in rows]

    async def account(self, acc: str) -> AccountRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.accounts WHERE acc = $1", acc)
        return None if row is None else AccountRecord(**dict(row))

    async def account_owner(self, acc: str) -> str | None:
        owner: str | None = await self._pool.fetchval(
            f"SELECT cif FROM {self._s}.accounts WHERE acc = $1", acc
        )
        return owner
