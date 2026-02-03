/**
 * VISE OS Booking Hooks Extension for Directus
 *
 * Handles booking lifecycle events, credit management, webhook triggers,
 * PII cleanup scheduling, and notifications.
 *
 * @see 002-DIRECTUS-SCHEMA.md for collection definitions
 * @see 014-QUEUE-ORCHESTRATOR.md for webhook integration
 */

/**
 * Valid booking request status values and transitions
 * @see 013-STATE-MACHINE.md
 */
const BOOKING_STATUSES = {
  PENDING: 'pending',
  QUEUED: 'queued',
  PROCESSING: 'processing',
  SLOT_FOUND: 'slot_found',
  BOOKING: 'booking',
  PAYMENT: 'payment',
  VERIFYING: 'verifying',
  COMPLETED: 'completed',
  FAILED: 'failed',
  EXPIRED: 'expired',
  CANCELLED: 'cancelled',
};

/**
 * Terminal statuses that don't transition further
 */
const TERMINAL_STATUSES = [
  BOOKING_STATUSES.COMPLETED,
  BOOKING_STATUSES.FAILED,
  BOOKING_STATUSES.EXPIRED,
  BOOKING_STATUSES.CANCELLED,
];

/**
 * Credit transaction types
 */
const CREDIT_TYPES = {
  PURCHASE: 'purchase',
  USAGE: 'usage',
  REFUND: 'refund',
  RESERVE: 'reserve',
  RELEASE: 'release',
};

/**
 * Collections referenced in hooks
 */
const COLLECTIONS = {
  AGENCIES: 'agencies',
  AGENCY_CREDITS: 'agency_credits',
  CREDIT_TRANSACTIONS: 'credit_transactions',
  APPLICANTS: 'applicants',
  BOOKING_REQUESTS: 'booking_requests',
  BOOKING_RESULTS: 'booking_results',
  BOT_ACCOUNTS: 'bot_accounts',
  SYSTEM_LOGS: 'system_logs',
};

/**
 * PII fields that should be redacted after expiry
 */
const PII_FIELDS = ['first_name', 'last_name', 'passport_number', 'phone', 'email'];

/**
 * Main hook registration function
 * @param {Object} context - Directus hook context
 */
export default ({ filter, action, schedule, env, logger, services }) => {
  const { ItemsService, MailService } = services;

  // Backend webhook URL from environment
  const BACKEND_WEBHOOK_URL = env.BACKEND_WEBHOOK_URL || 'http://api:8000/api/webhooks/directus';
  const WEBHOOK_SECRET = env.DIRECTUS_WEBHOOK_SECRET || '';
  const LOW_CREDIT_THRESHOLD = parseInt(env.LOW_CREDIT_THRESHOLD || '10', 10);
  const PII_RETENTION_HOURS = parseInt(env.PII_RETENTION_HOURS || '24', 10);

  // ============================================
  // BOOKING REQUEST HOOKS
  // ============================================

  /**
   * Before booking request create - validate and set defaults
   */
  filter(`${COLLECTIONS.BOOKING_REQUESTS}.items.create`, async (payload, meta, context) => {
    const { accountability, schema } = context;

    // Set defaults for new booking requests
    payload.status = payload.status || BOOKING_STATUSES.PENDING;
    payload.priority = payload.priority || 5;
    payload.attempts = payload.attempts || 0;
    payload.max_attempts = payload.max_attempts || 50;
    payload.slot_found_count = payload.slot_found_count || 0;
    payload.created_at = new Date().toISOString();
    payload.updated_at = new Date().toISOString();

    // Validate required fields
    if (!payload.agency_id) {
      throw new Error('agency_id is required for booking request');
    }
    if (!payload.applicant_id) {
      throw new Error('applicant_id is required for booking request');
    }
    if (!payload.target_system) {
      throw new Error('target_system is required for booking request');
    }
    if (!payload.target_country) {
      throw new Error('target_country is required for booking request');
    }
    if (!payload.visa_category) {
      throw new Error('visa_category is required for booking request');
    }

    logger.info(`Creating booking request for agency: ${payload.agency_id}`);

    return payload;
  });

  /**
   * After booking request create - reserve credits and queue for processing
   */
  action(`${COLLECTIONS.BOOKING_REQUESTS}.items.create`, async (meta, context) => {
    const { key, payload } = meta;
    const { database, schema, accountability } = context;

    try {
      // Reserve credit for this booking
      await reserveCredit(database, payload.agency_id, key, logger);

      // Send webhook to backend to queue the booking
      await sendWebhook({
        event: 'booking_requests.items.create',
        collection: COLLECTIONS.BOOKING_REQUESTS,
        key,
        payload: {
          id: key,
          agency_id: payload.agency_id,
          applicant_id: payload.applicant_id,
          target_system: payload.target_system,
          target_country: payload.target_country,
          target_location: payload.target_location,
          visa_category: payload.visa_category,
          priority: payload.priority,
          status: payload.status,
        },
      }, logger);

      // Log the creation
      await createSystemLog(database, {
        level: 'info',
        category: 'booking',
        event: 'booking_request_created',
        agency_id: payload.agency_id,
        booking_request_id: key,
        message: `Booking request created for ${payload.target_country} - ${payload.visa_category}`,
      });

    } catch (error) {
      logger.error(`Error in booking create hook: ${error.message}`);
      // Don't throw - allow the create to succeed but log the error
      await createSystemLog(database, {
        level: 'error',
        category: 'booking',
        event: 'booking_request_create_hook_error',
        agency_id: payload.agency_id,
        booking_request_id: key,
        message: error.message,
        details: { stack: error.stack },
      });
    }
  });

  /**
   * Before booking request update - validate status transitions
   */
  filter(`${COLLECTIONS.BOOKING_REQUESTS}.items.update`, async (payload, meta, context) => {
    const { keys } = meta;
    const { database, schema } = context;

    // If status is being updated, validate the transition
    if (payload.status) {
      for (const key of keys) {
        const currentBooking = await database
          .select('status', 'agency_id')
          .from(COLLECTIONS.BOOKING_REQUESTS)
          .where('id', key)
          .first();

        if (currentBooking) {
          const isValidTransition = validateStatusTransition(
            currentBooking.status,
            payload.status
          );

          if (!isValidTransition) {
            throw new Error(
              `Invalid status transition from ${currentBooking.status} to ${payload.status}`
            );
          }
        }
      }
    }

    // Update timestamp
    payload.updated_at = new Date().toISOString();

    return payload;
  });

  /**
   * After booking request update - handle status change side effects
   */
  action(`${COLLECTIONS.BOOKING_REQUESTS}.items.update`, async (meta, context) => {
    const { keys, payload } = meta;
    const { database, schema } = context;

    if (!payload.status) return;

    for (const key of keys) {
      try {
        const booking = await database
          .select('*')
          .from(COLLECTIONS.BOOKING_REQUESTS)
          .where('id', key)
          .first();

        if (!booking) continue;

        // Handle different status changes
        switch (payload.status) {
          case BOOKING_STATUSES.COMPLETED:
            await handleBookingCompleted(database, booking, logger);
            break;

          case BOOKING_STATUSES.FAILED:
          case BOOKING_STATUSES.EXPIRED:
            await handleBookingFailed(database, booking, payload.status, logger);
            break;

          case BOOKING_STATUSES.CANCELLED:
            await handleBookingCancelled(database, booking, logger);
            break;

          case BOOKING_STATUSES.SLOT_FOUND:
            await handleSlotFound(database, booking, logger);
            break;
        }

        // Send webhook for all status changes
        await sendWebhook({
          event: 'booking_requests.items.update',
          collection: COLLECTIONS.BOOKING_REQUESTS,
          key,
          payload: {
            id: key,
            agency_id: booking.agency_id,
            status: payload.status,
            previous_status: booking.status,
            target_system: booking.target_system,
            target_country: booking.target_country,
            attempts: booking.attempts,
            error_code: payload.error_code || booking.error_code,
            error_message: payload.error_message || booking.error_message,
          },
        }, logger);

      } catch (error) {
        logger.error(`Error handling booking update for ${key}: ${error.message}`);
        await createSystemLog(database, {
          level: 'error',
          category: 'booking',
          event: 'booking_update_hook_error',
          booking_request_id: key,
          message: error.message,
          details: { stack: error.stack },
        });
      }
    }
  });

  // ============================================
  // APPLICANT HOOKS
  // ============================================

  /**
   * Before applicant create - set expiry and validate
   */
  filter(`${COLLECTIONS.APPLICANTS}.items.create`, async (payload, meta, context) => {
    // Set PII expiry time (default 24 hours)
    const expiresAt = new Date();
    expiresAt.setHours(expiresAt.getHours() + PII_RETENTION_HOURS);
    payload.expires_at = expiresAt.toISOString();

    // Set default status
    payload.status = payload.status || 'pending';
    payload.created_at = new Date().toISOString();

    // Validate required fields
    if (!payload.agency_id) {
      throw new Error('agency_id is required for applicant');
    }
    if (!payload.first_name) {
      throw new Error('first_name is required for applicant');
    }
    if (!payload.last_name) {
      throw new Error('last_name is required for applicant');
    }
    if (!payload.passport_number) {
      throw new Error('passport_number is required for applicant');
    }

    return payload;
  });

  /**
   * After applicant create - trigger booking request creation if configured
   */
  action(`${COLLECTIONS.APPLICANTS}.items.create`, async (meta, context) => {
    const { key, payload } = meta;
    const { database } = context;

    try {
      // Send webhook to backend
      await sendWebhook({
        event: 'applicants.items.create',
        collection: COLLECTIONS.APPLICANTS,
        key,
        payload: {
          id: key,
          agency_id: payload.agency_id,
          target_country: payload.target_country,
          target_city: payload.target_city,
          visa_type: payload.visa_type,
          preferred_dates: payload.preferred_dates,
        },
      }, logger);

      await createSystemLog(database, {
        level: 'info',
        category: 'booking',
        event: 'applicant_created',
        agency_id: payload.agency_id,
        message: `Applicant created for ${payload.target_country}`,
        details: { applicant_id: key },
      });

    } catch (error) {
      logger.error(`Error in applicant create hook: ${error.message}`);
    }
  });

  // ============================================
  // AGENCY CREDITS HOOKS
  // ============================================

  /**
   * After agency credits update - check for low balance alerts
   */
  action(`${COLLECTIONS.AGENCY_CREDITS}.items.update`, async (meta, context) => {
    const { keys, payload } = meta;
    const { database } = context;

    for (const key of keys) {
      try {
        const credits = await database
          .select('*')
          .from(COLLECTIONS.AGENCY_CREDITS)
          .where('id', key)
          .first();

        if (!credits) continue;

        // Calculate available credits
        const availableCredits = credits.total_credits -
          credits.used_credits -
          credits.reserved_credits;

        // Check for low credit threshold
        if (availableCredits <= LOW_CREDIT_THRESHOLD && availableCredits >= 0) {
          await handleLowCredits(database, credits, availableCredits, logger);
        }

      } catch (error) {
        logger.error(`Error checking credits for ${key}: ${error.message}`);
      }
    }
  });

  // ============================================
  // BOT ACCOUNTS HOOKS
  // ============================================

  /**
   * After bot account update - handle ban detection
   */
  action(`${COLLECTIONS.BOT_ACCOUNTS}.items.update`, async (meta, context) => {
    const { keys, payload } = meta;
    const { database } = context;

    if (payload.status !== 'banned') return;

    for (const key of keys) {
      try {
        const account = await database
          .select('*')
          .from(COLLECTIONS.BOT_ACCOUNTS)
          .where('id', key)
          .first();

        if (!account) continue;

        await handleAccountBanned(database, account, logger);

      } catch (error) {
        logger.error(`Error handling banned account ${key}: ${error.message}`);
      }
    }
  });

  // ============================================
  // SCHEDULED JOBS
  // ============================================

  /**
   * PII Auto-Cleanup Job
   * Runs every hour to redact expired PII data
   * @see 002-DIRECTUS-SCHEMA.md Auto-Cleanup Job
   */
  schedule('0 * * * *', async () => {
    logger.info('Running PII cleanup job');

    try {
      // Get a database connection from services
      const knex = (await import('knex')).default;

      // Using raw query for atomic update
      // First, check for applicants with active processing bookings
      const result = await services.database.raw(`
        WITH active_processing AS (
          SELECT DISTINCT applicant_id
          FROM ${COLLECTIONS.BOOKING_REQUESTS}
          WHERE status IN ('processing', 'slot_found', 'booking', 'payment', 'verifying')
        ),
        to_extend AS (
          UPDATE ${COLLECTIONS.APPLICANTS}
          SET expires_at = NOW() + INTERVAL '1 hour',
              updated_at = NOW()
          WHERE expires_at < NOW()
            AND deleted_at IS NULL
            AND id IN (SELECT applicant_id FROM active_processing)
          RETURNING id
        ),
        to_redact AS (
          UPDATE ${COLLECTIONS.APPLICANTS}
          SET
            first_name = '[REDACTED]',
            last_name = '[REDACTED]',
            passport_number = '[REDACTED]',
            phone = '[REDACTED]',
            email = '[REDACTED]',
            deleted_at = NOW()
          WHERE expires_at < NOW()
            AND deleted_at IS NULL
            AND id NOT IN (SELECT applicant_id FROM active_processing)
          RETURNING id
        )
        SELECT
          (SELECT COUNT(*) FROM to_extend) as extended,
          (SELECT COUNT(*) FROM to_redact) as redacted
      `);

      const stats = result.rows?.[0] || { extended: 0, redacted: 0 };

      logger.info(`PII cleanup completed: ${stats.redacted} redacted, ${stats.extended} extended`);

      // Log the cleanup
      if (stats.redacted > 0 || stats.extended > 0) {
        await createSystemLog(services.database, {
          level: 'info',
          category: 'system',
          event: 'pii_cleanup_completed',
          message: `PII cleanup: ${stats.redacted} records redacted, ${stats.extended} records extended`,
          details: stats,
        });
      }

    } catch (error) {
      logger.error(`PII cleanup job failed: ${error.message}`);
      await createSystemLog(services.database, {
        level: 'error',
        category: 'system',
        event: 'pii_cleanup_failed',
        message: error.message,
        details: { stack: error.stack },
      });
    }
  });

  /**
   * Stale booking cleanup
   * Runs every 15 minutes to expire stuck bookings
   */
  schedule('*/15 * * * *', async () => {
    logger.info('Running stale booking cleanup');

    try {
      // Expire bookings stuck in processing states for too long
      const maxProcessingMinutes = parseInt(env.MAX_PROCESSING_MINUTES || '60', 10);
      const cutoffTime = new Date();
      cutoffTime.setMinutes(cutoffTime.getMinutes() - maxProcessingMinutes);

      const result = await services.database.raw(`
        UPDATE ${COLLECTIONS.BOOKING_REQUESTS}
        SET
          status = '${BOOKING_STATUSES.EXPIRED}',
          error_code = 'PROCESSING_TIMEOUT',
          error_message = 'Booking expired due to processing timeout',
          updated_at = NOW()
        WHERE status IN ('processing', 'slot_found', 'booking', 'payment', 'verifying')
          AND updated_at < ?
          AND status NOT IN (${TERMINAL_STATUSES.map(s => `'${s}'`).join(',')})
        RETURNING id, agency_id
      `, [cutoffTime.toISOString()]);

      const expiredBookings = result.rows || [];

      if (expiredBookings.length > 0) {
        logger.warn(`Expired ${expiredBookings.length} stale bookings`);

        // Release credits for expired bookings
        for (const booking of expiredBookings) {
          await releaseCredit(services.database, booking.agency_id, booking.id, logger);
        }

        await createSystemLog(services.database, {
          level: 'warning',
          category: 'booking',
          event: 'stale_bookings_expired',
          message: `Expired ${expiredBookings.length} stale bookings`,
          details: { booking_ids: expiredBookings.map(b => b.id) },
        });
      }

    } catch (error) {
      logger.error(`Stale booking cleanup failed: ${error.message}`);
    }
  });

  // ============================================
  // HELPER FUNCTIONS
  // ============================================

  /**
   * Validate booking status transition
   * @param {string} currentStatus - Current status
   * @param {string} newStatus - Target status
   * @returns {boolean} True if transition is valid
   */
  function validateStatusTransition(currentStatus, newStatus) {
    // Terminal states can't transition (except to themselves)
    if (TERMINAL_STATUSES.includes(currentStatus) && currentStatus !== newStatus) {
      return false;
    }

    // Any state can transition to failed, expired, or cancelled
    if ([BOOKING_STATUSES.FAILED, BOOKING_STATUSES.EXPIRED, BOOKING_STATUSES.CANCELLED].includes(newStatus)) {
      return true;
    }

    // Valid forward transitions
    const validTransitions = {
      [BOOKING_STATUSES.PENDING]: [BOOKING_STATUSES.QUEUED],
      [BOOKING_STATUSES.QUEUED]: [BOOKING_STATUSES.PROCESSING],
      [BOOKING_STATUSES.PROCESSING]: [BOOKING_STATUSES.SLOT_FOUND],
      [BOOKING_STATUSES.SLOT_FOUND]: [BOOKING_STATUSES.BOOKING, BOOKING_STATUSES.PROCESSING], // Can go back to processing if slot lost
      [BOOKING_STATUSES.BOOKING]: [BOOKING_STATUSES.PAYMENT],
      [BOOKING_STATUSES.PAYMENT]: [BOOKING_STATUSES.VERIFYING],
      [BOOKING_STATUSES.VERIFYING]: [BOOKING_STATUSES.COMPLETED],
    };

    const allowedNextStatuses = validTransitions[currentStatus] || [];
    return allowedNextStatuses.includes(newStatus);
  }

  /**
   * Reserve credit for a booking
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {string} bookingId - Booking request ID
   * @param {Object} logger - Logger instance
   */
  async function reserveCredit(database, agencyId, bookingId, logger) {
    const creditsToReserve = 1; // 1 credit per booking

    await database.transaction(async (trx) => {
      // Lock the credit record
      const credits = await trx
        .select('*')
        .from(COLLECTIONS.AGENCY_CREDITS)
        .where('agency_id', agencyId)
        .forUpdate()
        .first();

      if (!credits) {
        throw new Error(`No credit record found for agency ${agencyId}`);
      }

      const availableCredits = credits.total_credits -
        credits.used_credits -
        credits.reserved_credits;

      if (availableCredits < creditsToReserve) {
        throw new Error(`Insufficient credits. Available: ${availableCredits}, Required: ${creditsToReserve}`);
      }

      // Update reserved credits
      await trx(COLLECTIONS.AGENCY_CREDITS)
        .where('id', credits.id)
        .update({
          reserved_credits: credits.reserved_credits + creditsToReserve,
        });

      // Create transaction record
      await trx(COLLECTIONS.CREDIT_TRANSACTIONS).insert({
        id: generateUUID(),
        agency_id: agencyId,
        type: CREDIT_TYPES.RESERVE,
        amount: creditsToReserve,
        balance_after: availableCredits - creditsToReserve,
        reference_id: bookingId,
        description: 'Credit reserved for booking',
        created_at: new Date().toISOString(),
      });
    });

    logger.info(`Reserved ${creditsToReserve} credit for agency ${agencyId}, booking ${bookingId}`);
  }

  /**
   * Deduct credit after successful booking
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {string} bookingId - Booking request ID
   * @param {Object} logger - Logger instance
   */
  async function deductCredit(database, agencyId, bookingId, logger) {
    const creditsToDeduct = 1;

    await database.transaction(async (trx) => {
      const credits = await trx
        .select('*')
        .from(COLLECTIONS.AGENCY_CREDITS)
        .where('agency_id', agencyId)
        .forUpdate()
        .first();

      if (!credits) {
        throw new Error(`No credit record found for agency ${agencyId}`);
      }

      // Move from reserved to used
      await trx(COLLECTIONS.AGENCY_CREDITS)
        .where('id', credits.id)
        .update({
          reserved_credits: Math.max(0, credits.reserved_credits - creditsToDeduct),
          used_credits: credits.used_credits + creditsToDeduct,
          last_usage_at: new Date().toISOString(),
        });

      const newAvailable = credits.total_credits -
        (credits.used_credits + creditsToDeduct) -
        (credits.reserved_credits - creditsToDeduct);

      await trx(COLLECTIONS.CREDIT_TRANSACTIONS).insert({
        id: generateUUID(),
        agency_id: agencyId,
        type: CREDIT_TYPES.USAGE,
        amount: creditsToDeduct,
        balance_after: newAvailable,
        reference_id: bookingId,
        description: 'Credit charged for completed booking',
        created_at: new Date().toISOString(),
      });
    });

    logger.info(`Deducted ${creditsToDeduct} credit for agency ${agencyId}, booking ${bookingId}`);
  }

  /**
   * Release reserved credit after failed booking
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {string} bookingId - Booking request ID
   * @param {Object} logger - Logger instance
   */
  async function releaseCredit(database, agencyId, bookingId, logger) {
    const creditsToRelease = 1;

    await database.transaction(async (trx) => {
      const credits = await trx
        .select('*')
        .from(COLLECTIONS.AGENCY_CREDITS)
        .where('agency_id', agencyId)
        .forUpdate()
        .first();

      if (!credits) {
        logger.warn(`No credit record found for agency ${agencyId} when releasing`);
        return;
      }

      if (credits.reserved_credits < creditsToRelease) {
        logger.warn(`Not enough reserved credits to release for agency ${agencyId}`);
        return;
      }

      // Release reserved credits
      await trx(COLLECTIONS.AGENCY_CREDITS)
        .where('id', credits.id)
        .update({
          reserved_credits: credits.reserved_credits - creditsToRelease,
        });

      const newAvailable = credits.total_credits -
        credits.used_credits -
        (credits.reserved_credits - creditsToRelease);

      await trx(COLLECTIONS.CREDIT_TRANSACTIONS).insert({
        id: generateUUID(),
        agency_id: agencyId,
        type: CREDIT_TYPES.RELEASE,
        amount: creditsToRelease,
        balance_after: newAvailable,
        reference_id: bookingId,
        description: 'Credit released for failed/cancelled booking',
        created_at: new Date().toISOString(),
      });
    });

    logger.info(`Released ${creditsToRelease} credit for agency ${agencyId}, booking ${bookingId}`);
  }

  /**
   * Handle booking completion
   * @param {Object} database - Database connection
   * @param {Object} booking - Booking record
   * @param {Object} logger - Logger instance
   */
  async function handleBookingCompleted(database, booking, logger) {
    logger.info(`Handling booking completion for ${booking.id}`);

    // Deduct credit (move from reserved to used)
    await deductCredit(database, booking.agency_id, booking.id, logger);

    // Update completed_at timestamp
    await database(COLLECTIONS.BOOKING_REQUESTS)
      .where('id', booking.id)
      .update({ completed_at: new Date().toISOString() });

    // Send notification webhook
    await sendWebhook({
      event: 'booking.completed',
      collection: COLLECTIONS.BOOKING_REQUESTS,
      key: booking.id,
      payload: {
        id: booking.id,
        agency_id: booking.agency_id,
        applicant_id: booking.applicant_id,
        target_system: booking.target_system,
        target_country: booking.target_country,
        status: BOOKING_STATUSES.COMPLETED,
      },
    }, logger);

    // Log completion
    await createSystemLog(database, {
      level: 'info',
      category: 'booking',
      event: 'booking_completed',
      agency_id: booking.agency_id,
      booking_request_id: booking.id,
      message: `Booking completed for ${booking.target_country}`,
      details: {
        attempts: booking.attempts,
        target_system: booking.target_system,
      },
    });
  }

  /**
   * Handle booking failure
   * @param {Object} database - Database connection
   * @param {Object} booking - Booking record
   * @param {string} status - Final status (failed or expired)
   * @param {Object} logger - Logger instance
   */
  async function handleBookingFailed(database, booking, status, logger) {
    logger.info(`Handling booking failure for ${booking.id} with status ${status}`);

    // Release reserved credit
    await releaseCredit(database, booking.agency_id, booking.id, logger);

    // Send notification webhook
    await sendWebhook({
      event: `booking.${status}`,
      collection: COLLECTIONS.BOOKING_REQUESTS,
      key: booking.id,
      payload: {
        id: booking.id,
        agency_id: booking.agency_id,
        applicant_id: booking.applicant_id,
        target_system: booking.target_system,
        target_country: booking.target_country,
        status,
        error_code: booking.error_code,
        error_message: booking.error_message,
        attempts: booking.attempts,
      },
    }, logger);

    // Log failure
    await createSystemLog(database, {
      level: 'warning',
      category: 'booking',
      event: `booking_${status}`,
      agency_id: booking.agency_id,
      booking_request_id: booking.id,
      message: `Booking ${status} for ${booking.target_country}: ${booking.error_message || 'Unknown error'}`,
      details: {
        error_code: booking.error_code,
        attempts: booking.attempts,
        target_system: booking.target_system,
      },
    });
  }

  /**
   * Handle booking cancellation
   * @param {Object} database - Database connection
   * @param {Object} booking - Booking record
   * @param {Object} logger - Logger instance
   */
  async function handleBookingCancelled(database, booking, logger) {
    logger.info(`Handling booking cancellation for ${booking.id}`);

    // Release reserved credit
    await releaseCredit(database, booking.agency_id, booking.id, logger);

    // Send notification webhook
    await sendWebhook({
      event: 'booking.cancelled',
      collection: COLLECTIONS.BOOKING_REQUESTS,
      key: booking.id,
      payload: {
        id: booking.id,
        agency_id: booking.agency_id,
        status: BOOKING_STATUSES.CANCELLED,
      },
    }, logger);

    // Log cancellation
    await createSystemLog(database, {
      level: 'info',
      category: 'booking',
      event: 'booking_cancelled',
      agency_id: booking.agency_id,
      booking_request_id: booking.id,
      message: `Booking cancelled for ${booking.target_country}`,
    });
  }

  /**
   * Handle slot found event
   * @param {Object} database - Database connection
   * @param {Object} booking - Booking record
   * @param {Object} logger - Logger instance
   */
  async function handleSlotFound(database, booking, logger) {
    logger.info(`Slot found for booking ${booking.id}`);

    // Increment slot found count
    await database(COLLECTIONS.BOOKING_REQUESTS)
      .where('id', booking.id)
      .increment('slot_found_count', 1);

    // Send urgent notification
    await sendWebhook({
      event: 'booking.slot_found',
      collection: COLLECTIONS.BOOKING_REQUESTS,
      key: booking.id,
      payload: {
        id: booking.id,
        agency_id: booking.agency_id,
        target_system: booking.target_system,
        target_country: booking.target_country,
        priority: 'urgent',
      },
    }, logger);
  }

  /**
   * Handle low credit balance
   * @param {Object} database - Database connection
   * @param {Object} credits - Credit record
   * @param {number} availableCredits - Current available credits
   * @param {Object} logger - Logger instance
   */
  async function handleLowCredits(database, credits, availableCredits, logger) {
    logger.warn(`Low credits for agency ${credits.agency_id}: ${availableCredits} remaining`);

    // Get agency details for notification
    const agency = await database
      .select('name', 'contact_email', 'telegram_chat_id')
      .from(COLLECTIONS.AGENCIES)
      .where('id', credits.agency_id)
      .first();

    // Send notification webhook
    await sendWebhook({
      event: 'agency.low_credits',
      collection: COLLECTIONS.AGENCY_CREDITS,
      key: credits.id,
      payload: {
        agency_id: credits.agency_id,
        agency_name: agency?.name,
        available_credits: availableCredits,
        threshold: LOW_CREDIT_THRESHOLD,
        telegram_chat_id: agency?.telegram_chat_id,
      },
    }, logger);

    // Log alert
    await createSystemLog(database, {
      level: 'warning',
      category: 'booking',
      event: 'low_credits_alert',
      agency_id: credits.agency_id,
      message: `Low credit balance: ${availableCredits} credits remaining`,
      details: {
        total_credits: credits.total_credits,
        used_credits: credits.used_credits,
        reserved_credits: credits.reserved_credits,
      },
    });
  }

  /**
   * Handle account banned
   * @param {Object} database - Database connection
   * @param {Object} account - Bot account record
   * @param {Object} logger - Logger instance
   */
  async function handleAccountBanned(database, account, logger) {
    logger.warn(`Bot account banned: ${account.id} for system ${account.system}`);

    // Send alert webhook
    await sendWebhook({
      event: 'bot_account.banned',
      collection: COLLECTIONS.BOT_ACCOUNTS,
      key: account.id,
      payload: {
        id: account.id,
        system: account.system,
        country: account.country,
        ban_detected_at: new Date().toISOString(),
      },
    }, logger);

    // Update circuit breaker if exists
    await database(COLLECTIONS.CIRCUIT_BREAKERS)
      .where('domain', `${account.system}_${account.country}`)
      .update({
        failure_count: database.raw('failure_count + 1'),
        last_failure_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      })
      .catch(() => {
        // Circuit breaker may not exist, that's ok
      });

    // Log alert
    await createSystemLog(database, {
      level: 'error',
      category: 'system',
      event: 'bot_account_banned',
      message: `Bot account banned for ${account.system} in ${account.country}`,
      details: {
        account_id: account.id,
        consecutive_failures: account.consecutive_failures,
        health_score: account.health_score,
      },
    });
  }

  /**
   * Send webhook to backend
   * @param {Object} data - Webhook payload
   * @param {Object} logger - Logger instance
   */
  async function sendWebhook(data, logger) {
    try {
      const fetch = (await import('node-fetch')).default;

      const timestamp = Date.now().toString();
      const payload = JSON.stringify({
        ...data,
        timestamp,
      });

      // Generate HMAC signature if secret is configured
      const headers = {
        'Content-Type': 'application/json',
        'X-Directus-Event': data.event,
        'X-Directus-Timestamp': timestamp,
      };

      if (WEBHOOK_SECRET) {
        const crypto = await import('crypto');
        const signature = crypto
          .createHmac('sha256', WEBHOOK_SECRET)
          .update(payload)
          .digest('hex');
        headers['X-Directus-Signature'] = signature;
      }

      const response = await fetch(BACKEND_WEBHOOK_URL, {
        method: 'POST',
        headers,
        body: payload,
        timeout: 10000, // 10 second timeout
      });

      if (!response.ok) {
        logger.warn(`Webhook failed with status ${response.status}: ${await response.text()}`);
      } else {
        logger.debug(`Webhook sent successfully: ${data.event}`);
      }

    } catch (error) {
      logger.error(`Failed to send webhook: ${error.message}`);
      // Don't throw - webhook failures shouldn't break the main operation
    }
  }

  /**
   * Create a system log entry
   * @param {Object} database - Database connection
   * @param {Object} logData - Log entry data
   */
  async function createSystemLog(database, logData) {
    try {
      await database(COLLECTIONS.SYSTEM_LOGS).insert({
        id: generateUUID(),
        level: logData.level || 'info',
        category: logData.category || 'system',
        event: logData.event,
        agency_id: logData.agency_id || null,
        booking_request_id: logData.booking_request_id || null,
        message: logData.message,
        details: logData.details ? JSON.stringify(logData.details) : null,
        created_at: new Date().toISOString(),
      });
    } catch (error) {
      // Silently fail - don't let logging errors affect main operations
    }
  }

  /**
   * Generate a UUID v4
   * @returns {string} UUID string
   */
  function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
};
