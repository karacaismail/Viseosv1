/**
 * VISE OS Booking-Stats Endpoint for Directus
 *
 * Provides booking statistics aggregations for agencies, including
 * total requests, completion rates, durations, and per-country breakdowns.
 *
 * @see 002-DIRECTUS-SCHEMA.md for collection definitions
 * @see 018-OBSERVABILITY-SPEC.md for monitoring integration
 */

/**
 * Valid booking request status values
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
 * Collections referenced in stats queries
 */
const COLLECTIONS = {
  AGENCIES: 'agencies',
  BOOKING_REQUESTS: 'booking_requests',
  BOOKING_RESULTS: 'booking_results',
  AGENCY_CREDITS: 'agency_credits',
  CREDIT_TRANSACTIONS: 'credit_transactions',
};

/**
 * Terminal statuses representing completed processing
 */
const TERMINAL_STATUSES = [
  BOOKING_STATUSES.COMPLETED,
  BOOKING_STATUSES.FAILED,
  BOOKING_STATUSES.EXPIRED,
  BOOKING_STATUSES.CANCELLED,
];

/**
 * In-progress statuses
 */
const PENDING_STATUSES = [
  BOOKING_STATUSES.PENDING,
  BOOKING_STATUSES.QUEUED,
  BOOKING_STATUSES.PROCESSING,
  BOOKING_STATUSES.SLOT_FOUND,
  BOOKING_STATUSES.BOOKING,
  BOOKING_STATUSES.PAYMENT,
  BOOKING_STATUSES.VERIFYING,
];

/**
 * Parse range parameter to date cutoff
 * @param {string} range - Range string (e.g., '7d', '30d', '24h')
 * @returns {Date} Cutoff date
 */
function parseRange(range) {
  const now = new Date();
  const match = range.match(/^(\d+)([dhmw])$/);

  if (!match) {
    // Default to 7 days
    now.setDate(now.getDate() - 7);
    return now;
  }

  const value = parseInt(match[1], 10);
  const unit = match[2];

  switch (unit) {
    case 'h':
      now.setHours(now.getHours() - value);
      break;
    case 'd':
      now.setDate(now.getDate() - value);
      break;
    case 'w':
      now.setDate(now.getDate() - value * 7);
      break;
    case 'm':
      now.setMonth(now.getMonth() - value);
      break;
    default:
      now.setDate(now.getDate() - 7);
  }

  return now;
}

/**
 * Main endpoint registration function
 * @param {Object} router - Express router instance
 * @param {Object} context - Directus endpoint context
 */
export default (router, { services, database, getSchema, env, logger }) => {
  const { ItemsService } = services;

  /**
   * GET /
   * Get booking statistics for an agency
   *
   * Query params:
   *   - agency_id: UUID of the agency (required)
   *   - range: Time range string (e.g., '7d', '30d', '24h') - default '7d'
   *   - target_system: Filter by target system (optional)
   *   - target_country: Filter by target country (optional)
   */
  router.get('/', async (req, res) => {
    const startTime = Date.now();

    try {
      const { agency_id, range = '7d', target_system, target_country } = req.query;

      // Validate agency_id
      if (!agency_id) {
        return res.status(400).json({
          error: 'agency_id is required',
          timestamp: new Date().toISOString(),
        });
      }

      // Parse date range
      const cutoffDate = parseRange(range);

      // Build stats
      const stats = await getBookingStats(
        database,
        agency_id,
        cutoffDate,
        { target_system, target_country },
        logger
      );

      res.json({
        ...stats,
        query: {
          agency_id,
          range,
          from_date: cutoffDate.toISOString(),
          to_date: new Date().toISOString(),
          target_system: target_system || null,
          target_country: target_country || null,
        },
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    } catch (error) {
      logger.error(`Booking stats endpoint failed: ${error.message}`);

      res.status(500).json({
        error: 'Failed to retrieve booking statistics',
        message: error.message,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    }
  });

  /**
   * GET /summary
   * Get a simplified summary of booking statistics
   */
  router.get('/summary', async (req, res) => {
    const startTime = Date.now();

    try {
      const { agency_id, range = '7d' } = req.query;

      if (!agency_id) {
        return res.status(400).json({
          error: 'agency_id is required',
          timestamp: new Date().toISOString(),
        });
      }

      const cutoffDate = parseRange(range);
      const summary = await getBookingSummary(database, agency_id, cutoffDate, logger);

      res.json({
        ...summary,
        range,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    } catch (error) {
      logger.error(`Booking summary endpoint failed: ${error.message}`);

      res.status(500).json({
        error: 'Failed to retrieve booking summary',
        message: error.message,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    }
  });

  /**
   * GET /by-country
   * Get booking statistics grouped by target country
   */
  router.get('/by-country', async (req, res) => {
    const startTime = Date.now();

    try {
      const { agency_id, range = '7d' } = req.query;

      if (!agency_id) {
        return res.status(400).json({
          error: 'agency_id is required',
          timestamp: new Date().toISOString(),
        });
      }

      const cutoffDate = parseRange(range);
      const countryStats = await getStatsByCountry(database, agency_id, cutoffDate, logger);

      res.json({
        by_country: countryStats,
        range,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    } catch (error) {
      logger.error(`Booking by-country endpoint failed: ${error.message}`);

      res.status(500).json({
        error: 'Failed to retrieve country statistics',
        message: error.message,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    }
  });

  /**
   * GET /by-system
   * Get booking statistics grouped by target system (VFS, iDATA, BLS, KKOSMOS)
   */
  router.get('/by-system', async (req, res) => {
    const startTime = Date.now();

    try {
      const { agency_id, range = '7d' } = req.query;

      if (!agency_id) {
        return res.status(400).json({
          error: 'agency_id is required',
          timestamp: new Date().toISOString(),
        });
      }

      const cutoffDate = parseRange(range);
      const systemStats = await getStatsBySystem(database, agency_id, cutoffDate, logger);

      res.json({
        by_system: systemStats,
        range,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    } catch (error) {
      logger.error(`Booking by-system endpoint failed: ${error.message}`);

      res.status(500).json({
        error: 'Failed to retrieve system statistics',
        message: error.message,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    }
  });

  /**
   * GET /timeline
   * Get booking statistics over time (daily buckets)
   */
  router.get('/timeline', async (req, res) => {
    const startTime = Date.now();

    try {
      const { agency_id, range = '7d' } = req.query;

      if (!agency_id) {
        return res.status(400).json({
          error: 'agency_id is required',
          timestamp: new Date().toISOString(),
        });
      }

      const cutoffDate = parseRange(range);
      const timeline = await getStatsTimeline(database, agency_id, cutoffDate, logger);

      res.json({
        timeline,
        range,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    } catch (error) {
      logger.error(`Booking timeline endpoint failed: ${error.message}`);

      res.status(500).json({
        error: 'Failed to retrieve timeline statistics',
        message: error.message,
        timestamp: new Date().toISOString(),
        response_time_ms: Date.now() - startTime,
      });
    }
  });

  /**
   * Get comprehensive booking statistics
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {Date} cutoffDate - Date cutoff for stats
   * @param {Object} filters - Additional filters
   * @param {Object} logger - Logger instance
   * @returns {Object} Booking statistics
   */
  async function getBookingStats(database, agencyId, cutoffDate, filters, logger) {
    const { target_system, target_country } = filters;

    // Build base query conditions
    const buildConditions = (query) => {
      query
        .where('agency_id', agencyId)
        .where('created_at', '>=', cutoffDate.toISOString());

      if (target_system) {
        query.where('target_system', target_system);
      }
      if (target_country) {
        query.where('target_country', target_country);
      }

      return query;
    };

    // Get status counts
    const statusCounts = await buildConditions(
      database(COLLECTIONS.BOOKING_REQUESTS)
        .select('status')
        .count('* as count')
        .groupBy('status')
    );

    // Convert to object
    const statusMap = {};
    let totalRequests = 0;
    for (const row of statusCounts) {
      statusMap[row.status] = parseInt(row.count, 10);
      totalRequests += parseInt(row.count, 10);
    }

    const completed = statusMap[BOOKING_STATUSES.COMPLETED] || 0;
    const failed =
      (statusMap[BOOKING_STATUSES.FAILED] || 0) +
      (statusMap[BOOKING_STATUSES.EXPIRED] || 0);
    const cancelled = statusMap[BOOKING_STATUSES.CANCELLED] || 0;
    const pending = PENDING_STATUSES.reduce(
      (sum, status) => sum + (statusMap[status] || 0),
      0
    );

    // Calculate success rate (completed / (completed + failed))
    const successRate =
      completed + failed > 0 ? completed / (completed + failed) : 0;

    // Get average duration from booking_results
    const durationResult = await database(COLLECTIONS.BOOKING_RESULTS)
      .avg('total_duration_seconds as avg_duration')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .where('status', 'success')
      .first();

    const avgDurationSeconds = Math.round(
      parseFloat(durationResult?.avg_duration) || 0
    );

    // Get credits used
    const creditsResult = await database(COLLECTIONS.BOOKING_RESULTS)
      .sum('credits_charged as total_credits')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .first();

    const creditsUsed = parseInt(creditsResult?.total_credits, 10) || 0;

    // Get country breakdown
    const countryStats = await getStatsByCountry(database, agencyId, cutoffDate, logger);

    // Get attempt statistics
    const attemptStats = await database(COLLECTIONS.BOOKING_REQUESTS)
      .select(
        database.raw('AVG(attempts) as avg_attempts'),
        database.raw('MAX(attempts) as max_attempts'),
        database.raw('SUM(slot_found_count) as total_slots_found')
      )
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .whereIn('status', TERMINAL_STATUSES)
      .first();

    return {
      total_requests: totalRequests,
      completed,
      failed,
      cancelled,
      pending,
      success_rate: Math.round(successRate * 1000) / 1000,
      avg_duration_seconds: avgDurationSeconds,
      credits_used: creditsUsed,
      attempts: {
        average: Math.round(parseFloat(attemptStats?.avg_attempts) || 0),
        maximum: parseInt(attemptStats?.max_attempts, 10) || 0,
        total_slots_found: parseInt(attemptStats?.total_slots_found, 10) || 0,
      },
      by_country: countryStats,
    };
  }

  /**
   * Get simplified booking summary
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {Date} cutoffDate - Date cutoff
   * @param {Object} logger - Logger instance
   * @returns {Object} Summary stats
   */
  async function getBookingSummary(database, agencyId, cutoffDate, logger) {
    const statusCounts = await database(COLLECTIONS.BOOKING_REQUESTS)
      .select('status')
      .count('* as count')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .groupBy('status');

    const statusMap = {};
    let total = 0;
    for (const row of statusCounts) {
      statusMap[row.status] = parseInt(row.count, 10);
      total += parseInt(row.count, 10);
    }

    const completed = statusMap[BOOKING_STATUSES.COMPLETED] || 0;
    const failed =
      (statusMap[BOOKING_STATUSES.FAILED] || 0) +
      (statusMap[BOOKING_STATUSES.EXPIRED] || 0);
    const inProgress = PENDING_STATUSES.reduce(
      (sum, status) => sum + (statusMap[status] || 0),
      0
    );

    return {
      total_requests: total,
      completed,
      failed,
      in_progress: inProgress,
      success_rate: completed + failed > 0 ? Math.round((completed / (completed + failed)) * 100) : 0,
    };
  }

  /**
   * Get statistics grouped by country
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {Date} cutoffDate - Date cutoff
   * @param {Object} logger - Logger instance
   * @returns {Object} Country-grouped stats
   */
  async function getStatsByCountry(database, agencyId, cutoffDate, logger) {
    const countryData = await database(COLLECTIONS.BOOKING_REQUESTS)
      .select('target_country', 'status')
      .count('* as count')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .groupBy('target_country', 'status');

    // Aggregate by country
    const byCountry = {};
    for (const row of countryData) {
      const country = row.target_country;
      if (!byCountry[country]) {
        byCountry[country] = {
          completed: 0,
          failed: 0,
          pending: 0,
          total: 0,
        };
      }

      const count = parseInt(row.count, 10);
      byCountry[country].total += count;

      if (row.status === BOOKING_STATUSES.COMPLETED) {
        byCountry[country].completed += count;
      } else if (
        row.status === BOOKING_STATUSES.FAILED ||
        row.status === BOOKING_STATUSES.EXPIRED
      ) {
        byCountry[country].failed += count;
      } else if (PENDING_STATUSES.includes(row.status)) {
        byCountry[country].pending += count;
      }
    }

    // Calculate success rates per country
    for (const country of Object.keys(byCountry)) {
      const stats = byCountry[country];
      const finalized = stats.completed + stats.failed;
      stats.success_rate = finalized > 0
        ? Math.round((stats.completed / finalized) * 1000) / 1000
        : 0;
    }

    return byCountry;
  }

  /**
   * Get statistics grouped by target system
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {Date} cutoffDate - Date cutoff
   * @param {Object} logger - Logger instance
   * @returns {Object} System-grouped stats
   */
  async function getStatsBySystem(database, agencyId, cutoffDate, logger) {
    const systemData = await database(COLLECTIONS.BOOKING_REQUESTS)
      .select('target_system', 'status')
      .count('* as count')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .groupBy('target_system', 'status');

    // Aggregate by system
    const bySystem = {};
    for (const row of systemData) {
      const system = row.target_system;
      if (!bySystem[system]) {
        bySystem[system] = {
          completed: 0,
          failed: 0,
          pending: 0,
          total: 0,
        };
      }

      const count = parseInt(row.count, 10);
      bySystem[system].total += count;

      if (row.status === BOOKING_STATUSES.COMPLETED) {
        bySystem[system].completed += count;
      } else if (
        row.status === BOOKING_STATUSES.FAILED ||
        row.status === BOOKING_STATUSES.EXPIRED
      ) {
        bySystem[system].failed += count;
      } else if (PENDING_STATUSES.includes(row.status)) {
        bySystem[system].pending += count;
      }
    }

    // Calculate success rates per system
    for (const system of Object.keys(bySystem)) {
      const stats = bySystem[system];
      const finalized = stats.completed + stats.failed;
      stats.success_rate = finalized > 0
        ? Math.round((stats.completed / finalized) * 1000) / 1000
        : 0;
    }

    return bySystem;
  }

  /**
   * Get statistics timeline (daily buckets)
   * @param {Object} database - Knex database instance
   * @param {string} agencyId - Agency ID
   * @param {Date} cutoffDate - Date cutoff
   * @param {Object} logger - Logger instance
   * @returns {Array} Timeline data points
   */
  async function getStatsTimeline(database, agencyId, cutoffDate, logger) {
    // Group by date and status
    const timelineData = await database(COLLECTIONS.BOOKING_REQUESTS)
      .select(
        database.raw("DATE(created_at) as date"),
        'status'
      )
      .count('* as count')
      .where('agency_id', agencyId)
      .where('created_at', '>=', cutoffDate.toISOString())
      .groupBy(database.raw("DATE(created_at)"), 'status')
      .orderBy('date');

    // Aggregate by date
    const byDate = {};
    for (const row of timelineData) {
      const date = row.date;
      const dateStr = typeof date === 'string' ? date : date.toISOString().split('T')[0];

      if (!byDate[dateStr]) {
        byDate[dateStr] = {
          date: dateStr,
          total: 0,
          completed: 0,
          failed: 0,
          pending: 0,
        };
      }

      const count = parseInt(row.count, 10);
      byDate[dateStr].total += count;

      if (row.status === BOOKING_STATUSES.COMPLETED) {
        byDate[dateStr].completed += count;
      } else if (
        row.status === BOOKING_STATUSES.FAILED ||
        row.status === BOOKING_STATUSES.EXPIRED
      ) {
        byDate[dateStr].failed += count;
      } else if (PENDING_STATUSES.includes(row.status)) {
        byDate[dateStr].pending += count;
      }
    }

    // Convert to array and sort
    const timeline = Object.values(byDate).sort((a, b) =>
      a.date.localeCompare(b.date)
    );

    // Add success rate to each day
    for (const day of timeline) {
      const finalized = day.completed + day.failed;
      day.success_rate = finalized > 0
        ? Math.round((day.completed / finalized) * 1000) / 1000
        : 0;
    }

    return timeline;
  }
};
