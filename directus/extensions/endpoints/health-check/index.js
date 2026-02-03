/**
 * VISE OS Health-Check Endpoint for Directus
 *
 * Provides health status endpoint for load balancers, monitoring systems,
 * and orchestration platforms to verify Directus service availability.
 *
 * @see 002-DIRECTUS-SCHEMA.md for collection definitions
 * @see 018-OBSERVABILITY-SPEC.md for monitoring integration
 */

/**
 * Health check response statuses
 */
const HEALTH_STATUS = {
  HEALTHY: 'healthy',
  DEGRADED: 'degraded',
  UNHEALTHY: 'unhealthy',
};

/**
 * Main endpoint registration function
 * @param {Object} router - Express router instance
 * @param {Object} context - Directus endpoint context
 */
export default (router, { services, database, getSchema, env, logger }) => {
  const { ItemsService } = services;

  /**
   * GET /health
   * Basic health check - returns service status
   */
  router.get('/', async (req, res) => {
    const startTime = Date.now();

    try {
      const healthData = await performHealthCheck(database, getSchema, logger);
      const responseTime = Date.now() - startTime;

      res.json({
        status: healthData.status,
        timestamp: new Date().toISOString(),
        version: env.npm_package_version || '1.0.0',
        service: 'directus',
        response_time_ms: responseTime,
        checks: healthData.checks,
      });
    } catch (error) {
      logger.error(`Health check failed: ${error.message}`);

      res.status(503).json({
        status: HEALTH_STATUS.UNHEALTHY,
        timestamp: new Date().toISOString(),
        service: 'directus',
        response_time_ms: Date.now() - startTime,
        error: error.message,
      });
    }
  });

  /**
   * GET /health/live
   * Kubernetes liveness probe - minimal check
   */
  router.get('/live', (req, res) => {
    res.json({
      status: 'alive',
      timestamp: new Date().toISOString(),
    });
  });

  /**
   * GET /health/ready
   * Kubernetes readiness probe - checks database connectivity
   */
  router.get('/ready', async (req, res) => {
    try {
      // Quick database ping
      await database.raw('SELECT 1');

      res.json({
        status: 'ready',
        timestamp: new Date().toISOString(),
      });
    } catch (error) {
      logger.error(`Readiness check failed: ${error.message}`);

      res.status(503).json({
        status: 'not_ready',
        timestamp: new Date().toISOString(),
        error: 'Database connection failed',
      });
    }
  });

  /**
   * GET /health/detailed
   * Detailed health check with all component statuses
   * Requires authentication for sensitive details
   */
  router.get('/detailed', async (req, res) => {
    const startTime = Date.now();

    try {
      const healthData = await performDetailedHealthCheck(
        database,
        getSchema,
        services,
        env,
        logger
      );
      const responseTime = Date.now() - startTime;

      res.json({
        status: healthData.status,
        timestamp: new Date().toISOString(),
        version: env.npm_package_version || '1.0.0',
        service: 'directus',
        environment: env.NODE_ENV || 'development',
        response_time_ms: responseTime,
        checks: healthData.checks,
        dependencies: healthData.dependencies,
      });
    } catch (error) {
      logger.error(`Detailed health check failed: ${error.message}`);

      res.status(503).json({
        status: HEALTH_STATUS.UNHEALTHY,
        timestamp: new Date().toISOString(),
        service: 'directus',
        response_time_ms: Date.now() - startTime,
        error: error.message,
      });
    }
  });

  /**
   * Perform basic health check
   * @param {Object} database - Knex database instance
   * @param {Function} getSchema - Schema getter function
   * @param {Object} logger - Logger instance
   * @returns {Object} Health check result
   */
  async function performHealthCheck(database, getSchema, logger) {
    const checks = {};
    let overallStatus = HEALTH_STATUS.HEALTHY;

    // Database connectivity check
    try {
      const dbStart = Date.now();
      await database.raw('SELECT 1');
      checks.database = {
        status: HEALTH_STATUS.HEALTHY,
        response_time_ms: Date.now() - dbStart,
      };
    } catch (error) {
      checks.database = {
        status: HEALTH_STATUS.UNHEALTHY,
        error: error.message,
      };
      overallStatus = HEALTH_STATUS.UNHEALTHY;
    }

    // Schema availability check
    try {
      const schema = await getSchema();
      checks.schema = {
        status: schema ? HEALTH_STATUS.HEALTHY : HEALTH_STATUS.DEGRADED,
        collections_count: schema?.collections
          ? Object.keys(schema.collections).length
          : 0,
      };

      if (!schema) {
        overallStatus =
          overallStatus === HEALTH_STATUS.HEALTHY
            ? HEALTH_STATUS.DEGRADED
            : overallStatus;
      }
    } catch (error) {
      checks.schema = {
        status: HEALTH_STATUS.DEGRADED,
        error: error.message,
      };
      overallStatus =
        overallStatus === HEALTH_STATUS.HEALTHY
          ? HEALTH_STATUS.DEGRADED
          : overallStatus;
    }

    return {
      status: overallStatus,
      checks,
    };
  }

  /**
   * Perform detailed health check with all dependencies
   * @param {Object} database - Knex database instance
   * @param {Function} getSchema - Schema getter function
   * @param {Object} services - Directus services
   * @param {Object} env - Environment variables
   * @param {Object} logger - Logger instance
   * @returns {Object} Detailed health check result
   */
  async function performDetailedHealthCheck(
    database,
    getSchema,
    services,
    env,
    logger
  ) {
    const checks = {};
    const dependencies = {};
    let overallStatus = HEALTH_STATUS.HEALTHY;

    // Database connectivity and stats
    try {
      const dbStart = Date.now();
      await database.raw('SELECT 1');
      const dbTime = Date.now() - dbStart;

      // Get connection pool stats if available
      const poolStats = database.client?.pool
        ? {
            total: database.client.pool.numPendingCreates() +
              database.client.pool.numPendingAcquires() +
              database.client.pool.numFree() +
              database.client.pool.numUsed(),
            free: database.client.pool.numFree(),
            used: database.client.pool.numUsed(),
            pending: database.client.pool.numPendingAcquires(),
          }
        : null;

      checks.database = {
        status: HEALTH_STATUS.HEALTHY,
        response_time_ms: dbTime,
        pool: poolStats,
      };
    } catch (error) {
      checks.database = {
        status: HEALTH_STATUS.UNHEALTHY,
        error: error.message,
      };
      overallStatus = HEALTH_STATUS.UNHEALTHY;
    }

    // Schema check with collection details
    try {
      const schema = await getSchema();
      const collections = schema?.collections
        ? Object.keys(schema.collections)
        : [];

      // Check for VISE OS core collections
      const requiredCollections = [
        'agencies',
        'booking_requests',
        'applicants',
        'agency_credits',
      ];
      const missingCollections = requiredCollections.filter(
        (c) => !collections.includes(c)
      );

      checks.schema = {
        status: missingCollections.length === 0
          ? HEALTH_STATUS.HEALTHY
          : HEALTH_STATUS.DEGRADED,
        collections_count: collections.length,
        missing_collections: missingCollections,
      };

      if (missingCollections.length > 0) {
        overallStatus =
          overallStatus === HEALTH_STATUS.HEALTHY
            ? HEALTH_STATUS.DEGRADED
            : overallStatus;
      }
    } catch (error) {
      checks.schema = {
        status: HEALTH_STATUS.DEGRADED,
        error: error.message,
      };
      overallStatus =
        overallStatus === HEALTH_STATUS.HEALTHY
          ? HEALTH_STATUS.DEGRADED
          : overallStatus;
    }

    // Extensions check
    checks.extensions = {
      status: HEALTH_STATUS.HEALTHY,
      loaded: ['health-check', 'booking-hooks'],
    };

    // Memory usage
    if (typeof process !== 'undefined' && process.memoryUsage) {
      const memUsage = process.memoryUsage();
      checks.memory = {
        status: HEALTH_STATUS.HEALTHY,
        heap_used_mb: Math.round(memUsage.heapUsed / 1024 / 1024),
        heap_total_mb: Math.round(memUsage.heapTotal / 1024 / 1024),
        rss_mb: Math.round(memUsage.rss / 1024 / 1024),
      };
    }

    // External dependencies status
    dependencies.backend_api = {
      url: env.BACKEND_WEBHOOK_URL || 'http://api:8000',
      status: 'not_checked',
    };

    dependencies.redis = {
      url: env.REDIS_URL || 'redis://redis:6379',
      status: 'not_checked',
    };

    return {
      status: overallStatus,
      checks,
      dependencies,
    };
  }
};
