/* PC 10.2.3 dbo schema / SQL Server 2022. NON-PRODUCTION ONLY.
   Run in a dedicated query window connected to the intended PC database.
   Never run while this job is being rated, quoted or edited. No shutdown required.
   This script is NOT executed as part of pc-tools tests.

   1. Set @JobNumber. Leave @Apply = 0. Execute the entire batch: preview, ROLLBACK.
   2. Review selected period, BeanVersion, status, work items and outcome.
   3. To repair: fill the expected values from preview, set BOTH flags to 1,
      and execute the entire batch again. It COMMITs automatically only after checks.
   Do NOT add an extra COMMIT or run just the UPDATE. No transaction is left open.
   After a lost connection/commit acknowledgment, preview again; do not blindly retry.
   Reload the job afterward. SQL bypasses application callbacks/cache notifications;
   a browser reload is not proven to invalidate every server cache. Pricing stays as-is.
   Session isolation and lock timeout settings below persist in this query window.
*/
SET NOCOUNT ON;
IF @@TRANCOUNT <> 0
    THROW 51000, 'Use a query window with no existing transaction.', 1;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 3000;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;

DECLARE @JobNumber nvarchar(max) = N'0000000042'; -- Preserve leading zeros.
DECLARE @Apply bit = 0;                         -- 0 = preview/rollback; 1 = apply.
DECLARE @AcknowledgeNonProductionAndIdleJob bit = 0;
DECLARE @ExpectedPeriodID bigint = NULL;        -- From preview, never guessed.
DECLARE @ExpectedBeanVersion int = NULL;        -- From preview, never guessed.

IF @Apply IS NULL
    THROW 51000, 'Set Apply to 0 for preview or 1 for apply.', 1;
IF @JobNumber IS NULL OR DATALENGTH(@JobNumber) = 0
    OR DATALENGTH(LTRIM(RTRIM(@JobNumber))) <> DATALENGTH(@JobNumber)
    THROW 51000, 'Supply a nonempty exact job number without surrounding spaces.', 1;
IF @Apply = 1 AND (ISNULL(@AcknowledgeNonProductionAndIdleJob, 0) <> 1
    OR @ExpectedPeriodID IS NULL OR @ExpectedPeriodID <= 0
    OR @ExpectedBeanVersion IS NULL OR @ExpectedBeanVersion < 0
    OR @ExpectedBeanVersion >= 2147483647)
    THROW 51000, 'Apply requires non-production/idle acknowledgment and expected period/version.', 1;

DECLARE @JobID bigint, @PeriodID bigint, @JobCount int, @EdgeCount int, @PeriodCount int;
DECLARE @Quoting int, @Draft int, @TypeCount int, @Status int, @StatusCode varchar(255), @Version int;
DECLARE @Retired bigint, @Temporary bit, @WorkItemCount int, @Outcome varchar(40);
DECLARE @Before nvarchar(max), @After nvarchar(max), @ProtectedBefore nvarchar(max), @ProtectedAfter nvarchar(max);
DECLARE @Changed table (period_id bigint, old_status_id int, status_id int, old_bean_version int, bean_version int);

BEGIN TRY
    BEGIN TRANSACTION;

    SELECT @JobCount = COUNT(*), @JobID = MIN(ID)
    FROM (SELECT TOP (2) ID FROM dbo.pc_job WHERE JobNumber = @JobNumber AND Retired = 0 ORDER BY ID) j;
    IF @JobCount <> 1
        THROW 51000, 'Require exactly one active job-number match.', 1;
    IF NOT EXISTS (SELECT 1 FROM dbo.pc_job WHERE ID = @JobID
        AND CONVERT(nvarchar(max), JobNumber) COLLATE Latin1_General_100_BIN2 = @JobNumber COLLATE Latin1_General_100_BIN2
        AND DATALENGTH(CONVERT(nvarchar(max), JobNumber)) = DATALENGTH(@JobNumber))
        THROW 51000, 'Job number is not an exact match; preserve leading zeros.', 1;

    SELECT @EdgeCount = COUNT(*), @PeriodID = MIN(ForeignEntityID)
    FROM (SELECT TOP (3) ForeignEntityID FROM dbo.pc_jobpolicyperiod WHERE OwnerID = @JobID ORDER BY ID) e;
    IF @EdgeCount <> 1 OR @PeriodID IS NULL
        THROW 51000, 'Require exactly one selected-version edge.', 1;

    -- Preview all candidate periods; update locks are held until this short batch ends.
    SELECT TOP (101) p.ID AS period_id, p.PublicID AS public_id, p.JobID AS job_id,
        p.BranchNumber AS branch_number, p.TemporaryBranch AS temporary, p.Retired AS retired,
        p.Status AS status_id, s.TYPECODE AS status_code, p.BeanVersion AS bean_version,
        p.UpdateTime AS update_time, p.UpdateUserID AS update_user_id, p.EditLocked AS edit_locked,
        p.Locked AS locked, p.LockingColumn AS locking_column, p.QuoteMaturityLevel AS quote_maturity_level,
        p.QuoteHidden AS quote_hidden, p.TotalPremiumRPT AS total_premium, p.TotalCostRPT AS total_cost
    FROM dbo.pc_policyperiod p WITH (UPDLOCK)
    LEFT JOIN dbo.pctl_policyperiodstatus s ON s.ID = p.Status
    WHERE p.JobID = @JobID ORDER BY p.ID;
    SET @PeriodCount = @@ROWCOUNT;
    IF @PeriodCount > 100
        THROW 51000, 'More than 100 periods; manual investigation required.', 1;

    SELECT @Status = p.Status, @StatusCode = s.TYPECODE, @Version = p.BeanVersion,
        @Retired = p.Retired, @Temporary = p.TemporaryBranch
    FROM dbo.pc_policyperiod p
    LEFT JOIN dbo.pctl_policyperiodstatus s ON s.ID = p.Status
    WHERE p.ID = @PeriodID AND p.JobID = @JobID;
    IF @@ROWCOUNT <> 1
        THROW 51000, 'Selected period is not uniquely owned by this job.', 1;
    IF @Retired IS NULL OR @Retired <> 0 OR @Temporary IS NULL OR @Temporary <> 0
        OR @Version IS NULL OR @Version < 0
        THROW 51000, 'Selected period must be active, non-temporary and have a supported BeanVersion.', 1;

    SELECT @TypeCount = COUNT(*) FROM dbo.pctl_policyperiodstatus WHERE TYPECODE IN ('Quoting', 'Draft');
    SELECT @Quoting = MIN(ID) FROM dbo.pctl_policyperiodstatus
        WHERE TYPECODE COLLATE Latin1_General_100_BIN2 = 'Quoting' AND DATALENGTH(CONVERT(nvarchar(max), TYPECODE)) = DATALENGTH(N'Quoting');
    SELECT @Draft = MIN(ID) FROM dbo.pctl_policyperiodstatus
        WHERE TYPECODE COLLATE Latin1_General_100_BIN2 = 'Draft' AND DATALENGTH(CONVERT(nvarchar(max), TYPECODE)) = DATALENGTH(N'Draft');
    IF @TypeCount <> 2 OR @Quoting IS NULL OR @Draft IS NULL OR @Quoting = @Draft
        THROW 51000, 'Require unique exact Quoting and Draft typecodes.', 1;

    SELECT TOP (2) ID AS work_item_id, Status AS status_id, Subtype AS subtype_id
    FROM dbo.pc_basequotingworkitem WHERE PolicyPeriodID = @PeriodID ORDER BY ID;
    SET @WorkItemCount = @@ROWCOUNT;
    SET @Outcome = CASE WHEN @WorkItemCount > 0 THEN 'blocked-work-items'
        WHEN @Status = @Quoting AND @StatusCode COLLATE Latin1_General_100_BIN2 = 'Quoting' THEN 'eligible'
        WHEN @Status = @Draft AND @StatusCode COLLATE Latin1_General_100_BIN2 = 'Draft' THEN 'already-draft'
        ELSE 'not-applicable' END;

    SELECT @Before = (SELECT p.ID AS period_id, p.PublicID AS public_id, p.JobID AS job_id,
        p.BranchNumber AS branch_number, p.TemporaryBranch AS temporary, p.Retired AS retired,
        p.Status AS status_id, s.TYPECODE AS status_code, p.BeanVersion AS bean_version,
        p.UpdateTime AS update_time, p.UpdateUserID AS update_user_id, p.EditLocked AS edit_locked,
        p.Locked AS locked, p.LockingColumn AS locking_column, p.QuoteMaturityLevel AS quote_maturity_level,
        p.QuoteHidden AS quote_hidden, p.TotalPremiumRPT AS total_premium, p.TotalCostRPT AS total_cost
        FROM dbo.pc_policyperiod p LEFT JOIN dbo.pctl_policyperiodstatus s ON s.ID = p.Status
        WHERE p.ID = @PeriodID FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER);

    IF @Apply = 0
    BEGIN
        ROLLBACK TRANSACTION;
        SELECT DB_NAME() AS source_database, @JobNumber AS job_number, @JobID AS job_id,
            @PeriodID AS selected_period_id, @Version AS bean_version, @Outcome AS outcome,
            CAST(0 AS bit) AS changed, CAST(0 AS bit) AS committed, @Before AS before_json;
        RETURN;
    END;
    IF @PeriodID <> @ExpectedPeriodID
        THROW 51000, 'Selected period changed; preview again.', 1;
    IF @Outcome = 'already-draft'
    BEGIN
        ROLLBACK TRANSACTION;
        SELECT @Outcome AS outcome, CAST(0 AS bit) AS changed, CAST(0 AS bit) AS committed,
            @Before AS before_json, @Before AS after_json;
        RETURN; -- Idempotent no-op; does not claim who previously changed it.
    END;
    IF @Outcome <> 'eligible'
        THROW 51000, 'Only Quoting with no async work item can be reset.', 1;
    IF @Version = 2147483647
        THROW 51000, 'BeanVersion cannot be incremented safely.', 1;
    IF @Version <> @ExpectedBeanVersion
        THROW 51000, 'BeanVersion changed; preview again.', 1;
    IF EXISTS (SELECT 1 FROM sys.triggers WHERE parent_id = OBJECT_ID('dbo.pc_policyperiod') AND is_disabled = 0)
        THROW 51000, 'Enabled PolicyPeriod SQL trigger detected; reset refused.', 1;

    SELECT @ProtectedBefore = (SELECT ID, PublicID, JobID, BranchNumber, TemporaryBranch, Retired,
        UpdateUserID, EditLocked, Locked, LockingColumn, QuoteMaturityLevel, QuoteHidden, TotalPremiumRPT, TotalCostRPT
        FROM dbo.pc_policyperiod WHERE ID = @PeriodID FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER);

    -- The only business-table UPDATE in this script. Preserve pricing and unrelated flags.
    UPDATE dbo.pc_policyperiod
    SET Status = @Draft, BeanVersion = BeanVersion + 1, UpdateTime = SYSUTCDATETIME()
    OUTPUT inserted.ID, deleted.Status, inserted.Status, deleted.BeanVersion, inserted.BeanVersion INTO @Changed
    WHERE ID = @PeriodID AND JobID = @JobID AND Status = @Quoting AND BeanVersion = @ExpectedBeanVersion
        AND Retired = 0 AND TemporaryBranch = 0
        AND EXISTS (SELECT 1 FROM dbo.pc_jobpolicyperiod e WHERE e.OwnerID = @JobID AND e.ForeignEntityID = @PeriodID)
        AND NOT EXISTS (SELECT 1 FROM dbo.pc_basequotingworkitem w WHERE w.PolicyPeriodID = @PeriodID);
    IF @@ROWCOUNT <> 1 OR (SELECT COUNT(*) FROM @Changed) <> 1
        THROW 51000, 'Expected exactly one updated row; rolling back.', 1;
    IF NOT EXISTS (SELECT 1 FROM @Changed WHERE period_id = @PeriodID AND old_status_id = @Quoting
        AND status_id = @Draft AND old_bean_version = @ExpectedBeanVersion AND bean_version = @ExpectedBeanVersion + 1)
        THROW 51000, 'Unexpected UPDATE result; rolling back.', 1;

    SELECT @ProtectedAfter = (SELECT ID, PublicID, JobID, BranchNumber, TemporaryBranch, Retired,
        UpdateUserID, EditLocked, Locked, LockingColumn, QuoteMaturityLevel, QuoteHidden, TotalPremiumRPT, TotalCostRPT
        FROM dbo.pc_policyperiod WHERE ID = @PeriodID FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER);
    IF @ProtectedAfter IS NULL OR @ProtectedBefore COLLATE Latin1_General_100_BIN2 <> @ProtectedAfter COLLATE Latin1_General_100_BIN2
        OR DATALENGTH(@ProtectedBefore) <> DATALENGTH(@ProtectedAfter)
        OR NOT EXISTS (SELECT 1 FROM dbo.pc_policyperiod WHERE ID = @PeriodID AND Status = @Draft AND BeanVersion = @ExpectedBeanVersion + 1)
        OR (SELECT COUNT(*) FROM dbo.pc_jobpolicyperiod WHERE OwnerID = @JobID) <> 1
        OR NOT EXISTS (SELECT 1 FROM dbo.pc_jobpolicyperiod WHERE OwnerID = @JobID AND ForeignEntityID = @PeriodID)
        OR EXISTS (SELECT 1 FROM dbo.pc_basequotingworkitem WHERE PolicyPeriodID = @PeriodID)
        THROW 51000, 'Post-update verification failed; rolling back.', 1;

    SELECT @After = (SELECT p.ID AS period_id, p.PublicID AS public_id, p.JobID AS job_id,
        p.BranchNumber AS branch_number, p.TemporaryBranch AS temporary, p.Retired AS retired,
        p.Status AS status_id, s.TYPECODE AS status_code, p.BeanVersion AS bean_version,
        p.UpdateTime AS update_time, p.UpdateUserID AS update_user_id, p.EditLocked AS edit_locked,
        p.Locked AS locked, p.LockingColumn AS locking_column, p.QuoteMaturityLevel AS quote_maturity_level,
        p.QuoteHidden AS quote_hidden, p.TotalPremiumRPT AS total_premium, p.TotalCostRPT AS total_cost
        FROM dbo.pc_policyperiod p LEFT JOIN dbo.pctl_policyperiodstatus s ON s.ID = p.Status
        WHERE p.ID = @PeriodID FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER);
    COMMIT TRANSACTION;
    SELECT DB_NAME() AS source_database, @JobNumber AS job_number, @JobID AS job_id,
        @PeriodID AS selected_period_id, 'cleared' AS outcome, CAST(1 AS bit) AS changed,
        CAST(1 AS bit) AS committed, @Before AS before_json, @After AS after_json;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
