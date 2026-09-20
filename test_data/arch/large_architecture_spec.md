# ACME Enterprise Architecture Specification (Large)

**Version:** 3.4   **Status:** Approved   **Owner:** Enterprise Architecture Office

## 1. Document Purpose and Scope

This specification describes the target architecture for the ACME payment estate.
It is intentionally verbose: it consolidates decisions from twelve prior
documents, three vendor assessments and a regulatory review, and it is read by
architects, engineers, auditors and vendor managers.

## 2. Architecture Principles

All systems SHALL be built on the shared container platform. All data at rest
SHALL be encrypted. No new monoliths. Every integration SHALL be documented.

## 3. Shared Enterprise Platform Services

These are provided centrally and consumed by all domains. They are operated by
the Platform Engineering function and are not owned by any business domain.

### 3.1 Platform Service 1

Platform Service 1 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 1 scales horizontally and is deployed active-active across two
data centres.

### 3.2 Platform Service 2

Platform Service 2 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 2 scales horizontally and is deployed active-active across two
data centres.

### 3.3 Platform Service 3

Platform Service 3 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 3 scales horizontally and is deployed active-active across two
data centres.

### 3.4 Platform Service 4

Platform Service 4 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 4 scales horizontally and is deployed active-active across two
data centres.

### 3.5 Platform Service 5

Platform Service 5 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 5 scales horizontally and is deployed active-active across two
data centres.

### 3.6 Platform Service 6

Platform Service 6 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 6 scales horizontally and is deployed active-active across two
data centres.

### 3.7 Platform Service 7

Platform Service 7 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 7 scales horizontally and is deployed active-active across two
data centres.

### 3.8 Platform Service 8

Platform Service 8 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 8 scales horizontally and is deployed active-active across two
data centres.

### 3.9 Platform Service 9

Platform Service 9 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 9 scales horizontally and is deployed active-active across two
data centres.

### 3.10 Platform Service 10

Platform Service 10 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 10 scales horizontally and is deployed active-active across two
data centres.

### 3.11 Platform Service 11

Platform Service 11 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 11 scales horizontally and is deployed active-active across two
data centres.

### 3.12 Platform Service 12

Platform Service 12 is a shared capability operated on OpenShift. It is
monitored using Prometheus and Grafana, with logs shipped to the ELK Stack and
retained in Splunk for compliance. Consumers integrate over REST, with gRPC for
high-volume internal paths. The service is built on Spring Boot with Java 21 and
persists to PostgreSQL. Authentication uses the central identity provider under
RBAC. Service 12 scales horizontally and is deployed active-active across two
data centres.

## 4. Payment Domain Systems

The payment domain owns several systems that serve business capabilities rather
than the whole enterprise. These are in scope for business coverage auditing.

### 4.1 Payment Domain Component 1

Payment Domain Component 1 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 1, including request
acceptance, validation, and state tracking for stream 1. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-001 and to the settlement business capability.

### 4.2 Payment Domain Component 2

Payment Domain Component 2 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 2, including request
acceptance, validation, and state tracking for stream 2. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-002 and to the settlement business capability.

### 4.3 Payment Domain Component 3

Payment Domain Component 3 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 3, including request
acceptance, validation, and state tracking for stream 3. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-003 and to the settlement business capability.

### 4.4 Payment Domain Component 4

Payment Domain Component 4 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 4, including request
acceptance, validation, and state tracking for stream 4. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-004 and to the settlement business capability.

### 4.5 Payment Domain Component 5

Payment Domain Component 5 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 5, including request
acceptance, validation, and state tracking for stream 5. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-005 and to the settlement business capability.

### 4.6 Payment Domain Component 6

Payment Domain Component 6 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 6, including request
acceptance, validation, and state tracking for stream 6. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-006 and to the settlement business capability.

### 4.7 Payment Domain Component 7

Payment Domain Component 7 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 7, including request
acceptance, validation, and state tracking for stream 7. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-007 and to the settlement business capability.

### 4.8 Payment Domain Component 8

Payment Domain Component 8 is a microservice in the Payment Gateway Platform.
It is responsible for transaction processing for stream 8, including request
acceptance, validation, and state tracking for stream 8. It persists to
PostgreSQL, integrates with Mastercard, Elavon and CCnet over REST, and emits
settlement events consumed downstream. It is built in house, deployed on
OpenShift, and traces to FR-TR-008 and to the settlement business capability.

## 5. Compliance and Audit

The estate SHALL comply with PCI-DSS. Cardholder data SHALL remain within the
encryption service boundary. All access SHALL be role-based. Audit trails SHALL
be immutable and retained for seven years.

## 6. Technology Standards

Approved: Spring Boot, Java 21, PostgreSQL, Kafka, REST, gRPC, OpenShift,
Prometheus, Grafana, ELK Stack, Splunk, Docker.
Under assessment: Valkey, event sourcing.
Prohibited: unencrypted transports, shared database integration.

## 7. Domain Systems (continued)

### 7.9 Payment Domain Component 9

Payment Domain Component 9 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 9, including
reconciliation, exception queueing and financial reporting for ledger 9. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

### 7.10 Payment Domain Component 10

Payment Domain Component 10 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 10, including
reconciliation, exception queueing and financial reporting for ledger 10. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-002.

### 7.11 Payment Domain Component 11

Payment Domain Component 11 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 11, including
reconciliation, exception queueing and financial reporting for ledger 11. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-003.

### 7.12 Payment Domain Component 12

Payment Domain Component 12 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 12, including
reconciliation, exception queueing and financial reporting for ledger 12. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

### 7.13 Payment Domain Component 13

Payment Domain Component 13 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 13, including
reconciliation, exception queueing and financial reporting for ledger 13. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-002.

### 7.14 Payment Domain Component 14

Payment Domain Component 14 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 14, including
reconciliation, exception queueing and financial reporting for ledger 14. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-003.

### 7.15 Payment Domain Component 15

Payment Domain Component 15 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 15, including
reconciliation, exception queueing and financial reporting for ledger 15. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

### 7.16 Payment Domain Component 16

Payment Domain Component 16 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 16, including
reconciliation, exception queueing and financial reporting for ledger 16. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-002.

### 7.17 Payment Domain Component 17

Payment Domain Component 17 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 17, including
reconciliation, exception queueing and financial reporting for ledger 17. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-003.

### 7.18 Payment Domain Component 18

Payment Domain Component 18 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 18, including
reconciliation, exception queueing and financial reporting for ledger 18. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

### 7.19 Payment Domain Component 19

Payment Domain Component 19 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 19, including
reconciliation, exception queueing and financial reporting for ledger 19. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-002.

### 7.20 Payment Domain Component 20

Payment Domain Component 20 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 20, including
reconciliation, exception queueing and financial reporting for ledger 20. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-003.

### 7.21 Payment Domain Component 21

Payment Domain Component 21 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 21, including
reconciliation, exception queueing and financial reporting for ledger 21. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

### 7.22 Payment Domain Component 22

Payment Domain Component 22 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 22, including
reconciliation, exception queueing and financial reporting for ledger 22. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-002.

### 7.23 Payment Domain Component 23

Payment Domain Component 23 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 23, including
reconciliation, exception queueing and financial reporting for ledger 23. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-003.

### 7.24 Payment Domain Component 24

Payment Domain Component 24 is a microservice within the Payment Gateway
Platform. It is accountable for settlement handling for ledger 24, including
reconciliation, exception queueing and financial reporting for ledger 24. It
persists to PostgreSQL, consumes Kafka topics, and exposes a REST interface. It
is built in house and deployed on OpenShift. It traces to FR-SR-001.

## 8. Appendix A — Platform Consumption Matrix

Every domain component consumes the shared platform services described in
section 3. Consumption is mandatory for observability, container hosting and
identity. Deviation requires an approved architecture exception.

## 9. Appendix B — Vendor Register

OpenShift is procured commercially. Splunk is procured commercially. The ELK
Stack is open source, self-hosted. Grafana and Prometheus are open source,
self-hosted. Docker is open source. The remaining platform services are built
in house by Platform Engineering.
