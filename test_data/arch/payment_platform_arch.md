
**Architecture Outline for ACME Inc. Payment Gateway Platform**

### 1. **High-Level Architecture Overview**

The Payment Gateway Platform (PGP) is designed as a **Stateless Modular Microservices** architecture, utilizing a **Spring Boot stack in Java 21**, deployed on **OpenShift** in a **2 Replicas** environment. The architecture is hosted in **2 DataCentres** in an **Active-Active** configuration, ensuring high availability and low latency. The primary components are:

*   **Payment Orchestrator**
*   **Payment Routing Decision Engine**
*   **PGSP Gateway with Request/Response Adapter**
*   **PAN-Card Encryption Service**
*   **Storefront Management Service**
*   **Payment UI Service**
*   **Settlement Job Orchestrator**

### 2. **Component Responsibilities**

#### 2.1. **Payment Orchestrator**

*   Handles payment initiation, including:
    *   Request Acceptance and Validation
    *   Tenancy Mapping and State Tracking
    *   Initialization of Settlement Requests

**Relationships:**

*   Calls the **Payment Routing Decision Engine** to have the optimal PGSP/PSP selected for each initiated payment, rather than choosing a route itself
*   Receives the routing decision and the submission outcome back from the **Payment Routing Decision Engine**

#### 2.2. **Payment Routing Decision Engine**

*   Implements the **Rule-Based Routing** logic to determine the optimal PGSP/PSP for a given transaction, considering:
    *   Country of Transaction
    *   Transaction Currency
    *   Preferred Payment Method

**Relationships:**

*   Is called by the **Payment Orchestrator** to return a routing decision for an initiated payment
*   Calls the **PGSP Gateway with Request/Response Adapter** to execute the selected route; it performs no external PGSP/PSP communication itself

#### 2.3. **PGSP Gateway with Request/Response Adapter**

*   Provides a **standardized API** for integration with external PGSPs/PSPs (e.g., Mastercard, Elavon, CCnet)
*   Handles **Request/Response Adapters** for communication with external services

**Relationships:**

*   Calls the external PGSPs/PSPs (e.g., Mastercard, Elavon, CCnet) through the **Request/Response Adapters**, using the **standardized API** over **TLS 1.2+**
*   Is called by the **Payment Routing Decision Engine** with the routed payment request, and returns the submission response to the **Payment Orchestrator**; no other container communicates with an external PGSP/PSP directly

#### 2.4. **PAN-Card Encryption Service**

*   Secures **PAN (Primary Account Number)** and **CVV (Card Verification Value)** data, using:
    *   **AES-256 encryption** for PCI-DSS compliance
    *   **Valkey** as a **Cache** for in-memory storage without persistence

#### 2.5. **Storefront Management Service**

*   Manages storefronts, including:
    *   Tenancy/Short-Code assignment
    *   Payment Request tracking and reconciliation

#### 2.6. **Payment UI Service**

*   Provides the **rich, modern UI** for payment interaction, using AlpineJS
*   Facilitates **frontend-to-backend communication** for payment processing

#### 2.7. **Settlement Job Orchestrator**

The **Settlement Job Orchestrator** is the **batch and scheduled-processing container** of the platform. It is a **Spring Boot** microservice that embeds the **Quartz Job Scheduler** for durable, cluster-safe job execution. It exists because payment processing is not purely request/response: authorisations must be captured before they expire, unclaimed authorisations must be released, settlement files must be initiated with the PGSP on the scheme's cut-off, and the resulting positions must be reconciled against the platform's own records. Those are **time-driven, retryable, long-running** activities and they are deliberately kept out of the request path so that a slow PGSP settlement call can never add latency to a customer's payment.

The container hosts the following scheduled **jobs**. Each is a logical **component** within the Settlement Job Orchestrator container — not a separately deployable service — with its own Quartz job definition, trigger, misfire policy and retry semantics:

*   **Capture Job**
    *   Claims authorised payments for settlement, in full or in part (partial capture)
    *   Honours the authorisation window and the merchant's capture mode (automatic, delayed, manual)
    *   Idempotent per payment: a re-run must never double-capture
*   **Void Job**
    *   Releases authorisations that will not be captured, before they expire
    *   Handles both merchant-initiated cancellation and expiry-driven release
    *   Emits a reversal so the issuer's reserved funds are freed
*   **Settlement Initiation Job with PGSP**
    *   Builds and transmits settlement batches to the **PGSP** through the PGSP Gateway
    *   Aligns submission with each scheme's cut-off and settlement cycle
    *   Reconciles the PGSP acknowledgement back onto the settlement record
*   **Reconciliation Job**
    *   Matches the platform's internal payment and settlement records against the **PGSP** and scheme settlement reports
    *   Classifies unmatched items (in-platform-only, PGSP-only, amount mismatch) and raises them for operational review
    *   Produces the settlement position used by finance and by the monthly scheme reconciliation

**Scheduling and execution characteristics:**

*   **Job store:** Quartz persists triggers and job state in **PostgreSQL** using the **JDBC JobStore** (clustered mode), so schedules survive pod restarts and are not held in memory
*   **Clustering:** Runs as a Quartz **clustered** scheduler across the 2 replicas, so each trigger fires once and only once platform-wide rather than once per pod. This is what makes the container safe under the **2 Replica** OpenShift deployment and the **Active-Active** 2-datacentre topology
*   **Idempotency:** Every job is keyed on a business identifier (payment id, settlement batch id, reconciliation period) so an at-least-once trigger cannot produce a duplicate financial effect
*   **Retry and misfire:** Transient PGSP failures are retried with backoff; a missed trigger is handled by a misfire policy rather than silently skipped, and exhausted retries escalate to the operations queue
*   **Bounded batches:** Jobs process payments in bounded batches with checkpoints, so a large settlement run does not hold a single long transaction
*   **Observability:** Job execution outcome, duration, retry count and backlog depth are exported to the monitoring stack (see §7); a stalled or repeatedly failing job is an alertable event

**Relationships:**

*   Reads and writes payment, settlement and reconciliation state in **PostgreSQL**
*   Calls the **PGSP Gateway with Request/Response Adapter** to initiate settlement and to retrieve settlement reports — it never calls a PGSP directly
*   Obtains routing context from the **Payment Routing Decision Engine** where settlement must be directed to the PGSP that authorised the payment
*   Receives PAN/CVV only through the **PAN-Card Encryption Service**; the scheduler holds no card data of its own
*   Reports tenancy and storefront attribution via the **Storefront Management Service** so settlement and reconciliation remain attributable per storefront
*   Is triggered **not** by customer requests but by its own schedule; the **Payment Orchestrator** may enqueue an ad-hoc capture, but the job itself executes here

### 3. **Database and Cache**

*   **Primary Database:** PostgreSQL for storing:
    *   Payment Request and Settlement data
    *   Storefront and Tenancy information
    *   **Quartz scheduler state** (JDBC JobStore) for the Settlement Job Orchestrator: job definitions, triggers, and execution state, enabling clustered, durable scheduling rather than in-memory timers
    *   Capture, Void and Reconciliation run history, including per-job outcomes and retry counts
*   **Cache:** Valkey for in-memory storage of CVV data, ensuring PCI-DSS compliance without persistent storage.
    *   Valkey is **not** used as the job store: scheduled state must be durable and transactional, so it belongs in PostgreSQL. Valkey caching of job status is permitted for read-heavy operational dashboards only.

### 4. **Design Decisions, Techniques and Conventions**

**Architectural style:** The platform follows a **Stateless Modular Microservices** style — independently deployable services with their own data, communicating over REST, with no server-side session affinity.

**Design techniques** — the mechanisms by which the platform's quality attributes are met:

*   **Stateless Services** — no service holds client session state between requests. Session and idempotency state is externalised to **Valkey**, so any replica can serve any request and losing a pod loses no session. *Realizes:* **Horizontal Scalability** and **High Availability**. *Trade-off:* the external session store becomes a hard dependency on the request path.
*   **Redundancy / Replicas** — every service runs as **2 Replicas**, in **2 DataCentres**, in an **Active-Active** configuration. Failure of a pod, a node, or an entire data centre leaves the platform serving traffic. *Realizes:* **High Availability**. *Trade-off:* operational overhead of a second region and distributed debugging.
*   **Health-Checked Removal from Rotation** — an unhealthy replica is removed from the load-balancer rotation rather than being sent traffic, which is what makes the redundancy above actually work rather than merely existing. *Realizes:* **High Availability** and **Reliability**.
*   **Bounded Batch Processing with Checkpoints** — the Capture, Void and Settlement Initiation jobs process payments in bounded batches with checkpoints, so a long settlement run never holds one transaction open and a failed run resumes rather than restarting. *Realizes:* **Settlement Reliability** and **Recoverability**. *Trade-off:* partial completion is a state the operator must reason about.
*   **Idempotent Job Execution** — every scheduled job is keyed on a business identifier so an at-least-once trigger cannot produce a duplicate financial effect. *Realizes:* **Data Integrity**. *Trade-off:* every job must carry and persist its own key.
*   **Asynchronous Offload of Payment State Transitions** — capture, void and settlement transitions are executed as scheduled jobs rather than inline with the customer request, keeping PGSP latency out of the authorisation path. *Realizes:* **Authorisation Latency**. *Trade-off:* the platform is eventually consistent with the PGSP between a request and its settlement.

**Engineering conventions** — the organisation's own standards for how components are built and named:

*   **Container naming** — every deployable container is named `<company>-<product>-<domain>`. *Examples:* `acme-payments-web`, `acme-payments-api`, `acme-settlement-jobs`. **Mandatory.**
*   **Service naming** — every service image follows `<company>-<product>-<domain>-<tier>`. *Examples:* `acme-payments-orchestrator-api`. **Mandatory.**
*   **Environment naming** — every deployed environment is named `<env>-<region>-<tier>`, for example `prod-eu1-primary`.
*   **Semantic versioning** — all services version with semver and maintain **two minor releases** of backward compatibility before deprecation.
*   **Package structure** — every service organises code as `<domain>/{api,application,domain,infrastructure}`, keeping the domain model isolated from delivery and persistence.

### 5. **Deployment and Hosting**

*   **OpenShift Platform:** Hosts the microservices in a **2 Replicas** environment
*   **Active-Active Configuration:** Ensures high availability and low latency by replicating services across 2 DataCentres
*   **Containerization:** Utilizes Docker for containerization of microservices
*   **Networking:** Standardized communication protocols (e.g., REST API, gRPC) for microservices interaction
*   **Scheduler Clustering:** The Settlement Job Orchestrator runs its Quartz scheduler in **clustered mode** across both replicas and both data centres, coordinated through the shared PostgreSQL JDBC JobStore. Clustering is what prevents a job firing twice in an Active-Active topology; without it, the 2-replica deployment would double-fire every trigger.

### 6. **Testing and Validation**

*   **Integration Tests:** Verify communication between microservices and external PGSP/PSPs
*   **Security Tests:** Validate PCI-DSS compliance and data encryption
*   **Load Testing:** Simulate high traffic loads to ensure performance and scalability
*   **Regression Testing:** Ensure compatibility with updates to PGSPs/PSPs and other external services
*   **Scheduler Tests:** Verify that a trigger fires exactly once across the clustered scheduler, that a re-run of the Capture, Void and Settlement Initiation jobs is idempotent, and that a misfired or missed trigger is retried rather than silently dropped
*   **Reconciliation Tests:** Replay a settlement period containing known unmatched items and assert that the Reconciliation Job classifies each of them correctly instead of absorbing them into a matched total

### 7. **Monitoring and Management**

*   **Monitoring Tools:** Utilize Prometheus, Grafana, and ELK Stack for real-time monitoring and alerts
*   **Logging:** Implement centralized logging with Splunk or ELK Stack
*   **Alerting:** Configure alerts for critical events (e.g., service unavailability, security breaches)
*   **Job Monitoring:** Track scheduler health separately from request-path health — trigger latency, job failure and retry counts, misfire count, and backlog depth for pending capture, void and settlement work. A stalled Settlement Job Orchestrator produces no error to a customer yet silently stops settling funds, so it must be alerted on absence of success, not only on an error.
*   **Operational Dashboard:** Surface the operations queue of unmatched reconciliation items and exhausted-retry jobs for manual intervention

### 8. **Security**

*   **Firewall Configuration:** Restrict access to microservices and database based on RBAC
*   **Encryption:** Use TLS 1.2+ for communication between microservices and external services
*   **Access Control:** Implement RBAC for administrative and backend interfaces

### 9. **Scalability and Performance**

*   **Horizontal Scaling:** Utilize OpenShift's built-in scaling capabilities to handle increased traffic
*   **Vertical Scaling:** Implement auto-scaling for individual microservices based on resource utilization
*   **Performance Monitoring:** Track latency, throughput, and response times for critical components
*   **Batch vs. Request Scaling:** The Settlement Job Orchestrator scales on **backlog depth and job latency**, not on request concurrency. Its trigger rate is fixed by the scheme settlement calendar, so adding replicas does not increase throughput the way it does for the stateless request-path services. Settlement throughput is therefore raised by **widening the bounded batch size** and by parallelising across partitions (per scheme, per currency), while remaining safe under Quartz clustering.
*   **Separating Batch Load from the Request Path:** Because settlement and reconciliation runs are isolated in their own container, a long settlement batch or a slow PGSP report retrieval cannot consume capacity from, or add latency to, authorisation and capture requests. This separation is a deliberate scalability decision, not only a scheduling one.

### 10. **Documentation and Maintenance**

*   **Architecture Document:** Maintain a comprehensive architecture document for all stakeholders
*   **Change Management:** Implement a change management process to ensure smooth transitions between service versions
*   **Maintenance Plan:** Schedule regular maintenance, updates, and security patches for the entire platform
