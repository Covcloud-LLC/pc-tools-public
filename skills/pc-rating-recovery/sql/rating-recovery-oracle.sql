/* UNEXECUTED ORACLE TRANSLATION, not Python-tool Oracle support.
   Assumes the PC 10.2.3 names below, numeric Retired/TemporaryBranch flags,
   integer BeanVersion, and an UpdateTime accepting a UTC timestamp without zone.
   Verify the actual schema and application time convention before applying.
   Use a NEW, CLEAN session in the table-owning schema, without synonym redirects.
   Preview: leave v_apply=0. Apply: copy expected values, set both flags to 1.
   Run the WHOLE block, never an isolated UPDATE; no extra COMMIT.
   Oracle SERIALIZABLE does not lock absent rows: concurrent async-item or extra
   selected-edge inserts can escape checks. Keep ALL work on this job idle.
   FOR UPDATE WAIT 3 bounds ordinary row-lock waits, not exclusive table-lock waits or total time.
   SQL skips application callbacks/cache notifications; reload and inspect afterward.
   SQL*Plus/SQLcl/SQL Developer Run Script: SET SERVEROUTPUT ON and final / are
   client commands. Other clients: omit both and enable DBMS_OUTPUT themselves.
*/
SET SERVEROUTPUT ON;

DECLARE
    v_job_number       VARCHAR2(128) := '0000000042'; -- Synthetic; replace, keep zeros.
    v_apply            NUMBER(1) := 0;
    v_ack_dev_idle      NUMBER(1) := 0;
    v_expected_period  NUMBER := NULL; -- From preview
    v_expected_version NUMBER := NULL; -- From preview
    v_job_id NUMBER; v_period_id NUMBER; v_count NUMBER;
    v_quoting NUMBER; v_draft NUMBER;
    v_before pc_policyperiod%ROWTYPE;
    v_after pc_policyperiod%ROWTYPE;
    v_commit_attempted BOOLEAN := FALSE;

    PROCEDURE refuse(p_message VARCHAR2) IS
    BEGIN
        RAISE_APPLICATION_ERROR(-20001, p_message);
    END;
    PROCEDURE evidence(p_label VARCHAR2, p_row pc_policyperiod%ROWTYPE) IS
    BEGIN
        DBMS_OUTPUT.PUT_LINE(p_label || ' job=' || v_job_number || ' jobID=' || v_job_id ||
            ' periodID=' || p_row.ID || ' statusID=' || p_row.Status ||
            ' BeanVersion=' || p_row.BeanVersion || ' UpdateTime=' ||
            TO_CHAR(p_row.UpdateTime, 'YYYY-MM-DD HH24:MI:SS') ||
            ' TotalPremium=' || NVL(TO_CHAR(p_row.TotalPremiumRPT), 'NULL') ||
            ' TotalCost=' || NVL(TO_CHAR(p_row.TotalCostRPT), 'NULL'));
    END;
BEGIN
    -- Outside our rollback handler: never roll back another transaction on refusal.
    IF DBMS_TRANSACTION.LOCAL_TRANSACTION_ID(FALSE) IS NOT NULL THEN
        refuse('Use a new clean session with no existing transaction.');
    END IF;
    EXECUTE IMMEDIATE 'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE';

    BEGIN
        IF v_job_number IS NULL OR NVL(v_apply, -1) NOT IN (0, 1) THEN
            refuse('Supply an exact job number and Apply=0 or 1.');
        END IF;
        SELECT COUNT(*), MIN(ID) INTO v_count, v_job_id FROM pc_job
         WHERE Retired = 0 AND JobNumber = v_job_number
           AND LENGTH(JobNumber) = LENGTH(v_job_number)
           AND NLSSORT(JobNumber, 'NLS_SORT=BINARY') = NLSSORT(v_job_number, 'NLS_SORT=BINARY');
        IF v_count <> 1 THEN refuse('Require exactly one exact active job match.'); END IF;
        SELECT ID INTO v_job_id FROM pc_job WHERE ID = v_job_id AND Retired = 0
           FOR UPDATE WAIT 3;

        -- Actual selected version; never choose the newest period ID or timestamp.
        SELECT COUNT(*) INTO v_count FROM pc_jobpolicyperiod WHERE OwnerID = v_job_id;
        IF v_count <> 1 THEN refuse('Require exactly one selected-period edge.'); END IF;
        SELECT ForeignEntityID INTO v_period_id FROM pc_jobpolicyperiod
         WHERE OwnerID = v_job_id FOR UPDATE WAIT 3;
        SELECT COUNT(*) INTO v_count FROM pc_policyperiod WHERE JobID = v_job_id;
        IF v_count > 100 THEN refuse('More than 100 periods; manual investigation required.'); END IF;
        SELECT * INTO v_before FROM pc_policyperiod
         WHERE ID = v_period_id AND JobID = v_job_id AND Retired = 0 AND TemporaryBranch = 0
           FOR UPDATE WAIT 3;
        IF v_before.BeanVersion IS NULL OR v_before.BeanVersion < 0
           OR v_before.BeanVersion > 2147483647 OR v_before.BeanVersion <> TRUNC(v_before.BeanVersion) THEN
            refuse('Unsupported BeanVersion.');
        END IF;

        SELECT COUNT(*),
            MAX(CASE WHEN NLSSORT(TYPECODE, 'NLS_SORT=BINARY') = NLSSORT('Quoting', 'NLS_SORT=BINARY')
                     AND LENGTH(TYPECODE) = 7 THEN ID END),
            MAX(CASE WHEN NLSSORT(TYPECODE, 'NLS_SORT=BINARY') = NLSSORT('Draft', 'NLS_SORT=BINARY')
                     AND LENGTH(TYPECODE) = 5 THEN ID END)
          INTO v_count, v_quoting, v_draft FROM pctl_policyperiodstatus
         WHERE TYPECODE IN ('Quoting', 'Draft');
        IF v_count <> 2 OR v_quoting IS NULL OR v_draft IS NULL OR v_quoting = v_draft THEN
            refuse('Require unique exact Quoting and Draft typecodes.');
        END IF;
        SELECT COUNT(*) INTO v_count FROM pc_basequotingworkitem WHERE PolicyPeriodID = v_period_id;
        evidence('BEFORE', v_before);
        DBMS_OUTPUT.PUT_LINE('QuotingID=' || v_quoting || ' DraftID=' || v_draft || ' WorkItems=' || v_count);
        IF v_apply = 0 THEN
            ROLLBACK;
            DBMS_OUTPUT.PUT_LINE('PREVIEW: no UPDATE; apply requires Quoting and zero work items.');
            RETURN;
        END IF;
        IF NVL(v_ack_dev_idle, 0) <> 1 OR v_expected_period IS NULL OR v_expected_period <> v_period_id
           OR v_expected_version IS NULL OR v_expected_version < 0 OR v_expected_version >= 2147483647
           OR v_expected_version <> TRUNC(v_expected_version) THEN
            refuse('Apply requires DEV/idle acknowledgment and expected period/version.');
        END IF;
        IF v_count <> 0 THEN refuse('Async work item exists; investigate instead of resetting.'); END IF;
        IF v_before.Status = v_draft THEN
            ROLLBACK;
            evidence('ALREADY DRAFT; no change', v_before);
            RETURN;
        END IF;
        IF v_before.Status IS NULL OR v_before.Status <> v_quoting
           OR v_before.BeanVersion <> v_expected_version OR v_before.BeanVersion = 2147483647 THEN
            refuse('Require Quoting and unchanged, incrementable BeanVersion.');
        END IF;
        SELECT COUNT(*) INTO v_count FROM all_triggers
         WHERE TABLE_OWNER = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
           AND TABLE_NAME = 'PC_POLICYPERIOD' AND STATUS = 'ENABLED';
        IF v_count <> 0 THEN refuse('Enabled PolicyPeriod trigger; reset refused.'); END IF;

        UPDATE pc_policyperiod
           SET Status = v_draft, BeanVersion = BeanVersion + 1,
               UpdateTime = CAST(SYS_EXTRACT_UTC(SYSTIMESTAMP) AS TIMESTAMP)
         WHERE ID = v_period_id AND JobID = v_job_id
           AND Status = v_quoting AND BeanVersion = v_expected_version
           AND Retired = 0 AND TemporaryBranch = 0
           AND EXISTS (SELECT 1 FROM pc_jobpolicyperiod e
                       WHERE e.OwnerID = v_job_id AND e.ForeignEntityID = v_period_id)
           AND NOT EXISTS (SELECT 1 FROM pc_basequotingworkitem w WHERE w.PolicyPeriodID = v_period_id);
        IF SQL%ROWCOUNT <> 1 THEN refuse('Expected exactly one updated row; rolling back.'); END IF;
        SELECT * INTO v_after FROM pc_policyperiod WHERE ID = v_period_id AND JobID = v_job_id;
        IF v_after.Status IS NULL OR v_after.Status <> v_draft
           OR v_after.BeanVersion IS NULL OR v_after.BeanVersion <> v_before.BeanVersion + 1 THEN
            refuse('Post-update status/version verification failed.');
        END IF;
        SELECT COUNT(*) INTO v_count FROM pc_policyperiod
         WHERE ID = v_period_id AND JobID = v_job_id
           AND DECODE(PublicID, v_before.PublicID, 1, 0) = 1
           AND DECODE(BranchNumber, v_before.BranchNumber, 1, 0) = 1
           AND DECODE(TemporaryBranch, v_before.TemporaryBranch, 1, 0) = 1
           AND DECODE(Retired, v_before.Retired, 1, 0) = 1
           AND DECODE(UpdateUserID, v_before.UpdateUserID, 1, 0) = 1
           AND DECODE(EditLocked, v_before.EditLocked, 1, 0) = 1
           AND DECODE(Locked, v_before.Locked, 1, 0) = 1
           AND DECODE(LockingColumn, v_before.LockingColumn, 1, 0) = 1
           AND DECODE(QuoteMaturityLevel, v_before.QuoteMaturityLevel, 1, 0) = 1
           AND DECODE(QuoteHidden, v_before.QuoteHidden, 1, 0) = 1
           AND DECODE(TotalPremiumRPT, v_before.TotalPremiumRPT, 1, 0) = 1
           AND DECODE(TotalCostRPT, v_before.TotalCostRPT, 1, 0) = 1
           AND (SELECT COUNT(*) FROM pc_jobpolicyperiod WHERE OwnerID = v_job_id) = 1
           AND EXISTS (SELECT 1 FROM pc_jobpolicyperiod WHERE OwnerID = v_job_id AND ForeignEntityID = v_period_id)
           AND NOT EXISTS (SELECT 1 FROM pc_basequotingworkitem WHERE PolicyPeriodID = v_period_id);
        IF v_count <> 1 THEN refuse('Protected fields, selection or work-item verification failed.'); END IF;
        evidence('VERIFIED BEFORE COMMIT', v_after);
        v_commit_attempted := TRUE;
        COMMIT;
        DBMS_OUTPUT.PUT_LINE('COMMITTED: one period reset.');
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            IF v_commit_attempted THEN
                DBMS_OUTPUT.PUT_LINE('Commit attempted; outcome may be unknown. Preview before retrying.');
            ELSE
                DBMS_OUTPUT.PUT_LINE('FAILED: transaction rolled back.');
            END IF;
            RAISE;
    END;
END;
/
