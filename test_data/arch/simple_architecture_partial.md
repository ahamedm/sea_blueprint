A Microservices Architecture for the Payment Platform.
Storefronts are first class entities, stored by a StoreFront Management microservice.
Payment Requests will land in the Payment Orchestrator Service which takes decisions based on the Rules in Rule Engine Service.
Only PGSP/PSP reviewed with Enterprise CyberSecurity Standards/Expectations will be integrated. 
As of now only Elavon, Ayden are whitelisted and allowed. Other providers are in process of Security review.
All the intranet and internet endpoints are protected and TLS-secured following the Zero Trust enterprise principle.
Oracle 19C will be the Datastore with distinct Schema for each Microservice, following Schema per Service technique. PII Data will be secured with RBAC and Anonymized wherever applicable following Customer Data Protection policy of Enterprise.
Payment Requests processing State will be tracked with an Internal State machine of Payment Orchestrator Service.
In fact each Service which processes the Payment Request will have an internal State Machine.
Payment Requests might be forwarded to external Fraud Mitigation Service provider like CyberSource based on the rules.
Payment-UI Service will have a highly functional, secured Payment Page developed with PreactJS framework. 
Payment Requests routing to backend PGSPs or PSPs will be controlled by Rule Engines.
PSP-Gateway Service will act as Gateway to forward the request to the PGSPs or PSPs with the necessary
Request-Response adapters. Access Logs from all Services will be published to CSOC SIEM, adhering to Enterprise principle.
Card Holder Data will be Encryped with AES-256 algorithm, powered by Google Tink before Storing to database.
CVV will be stored in Non-Persistence Cache powered by Highly Available Valkey Service.
A Job Scheduler Service will host all the Jobs like Storefront-Billing, Settlement, Capture-Initiation, Reconciliation. Job Scheduler will use Quartz Engine to schedule Jobs on timely , clustered fashion across nodes.
All Communications between Microservices will be mTLS protected.
Rules of the Rule Engine will be maintained in the Administration Service.
Payment Orchestrator, Payment-UI will be exposed via Ingress Gateway, based on Istio Technology.
Stateless Services, Asynchronous Processing, gRPC for Service-Service Communication should provide 140-150 TPS.
Istio Gateway , based on Envoy technology is also battle tested for better throughput (TPS).
The Services are protected by OAuth2 provided by KeyCloak Service.
All Microservices are Stateless and Containerized with Docker. There will be Redundancy/Replicas for the Microservices with Horizontal Scaling enabled. Microservices will be hosted on OpenShift Container Orchestrator Platform.
Payment Platform will be hosted in 2 Data Centres on VMware Tanzu Kubernetes Grid (TKG).
VMware Tanzu Kubernetes Grid (TKG) will be dedicated for such CHD handling workloads due to PCI-DSS Regulatory Requirements.
Decision is to go with On-Premises Deployment and not Public Cloud Providers due to affinity with existing Consumer Storefronts.
Microservices Technology will be Java instead of Go/Python due to Engineering Preference and Operational Marutity on the Platform in Enterprise. No Performance degradation is expected due to Java.
