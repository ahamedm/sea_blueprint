A Microservices Architecture for the Payment Platform.
Storefronts are first class entities, stored by a StoreFront Management microservice.
Payment Requests will land in the Payment Orchestrator Service which takes decisions based on the Rules in Rule Engine Service.

Oracle 19C will be the Datastore with distinct Schema for each Microservice, following Schema per Service technique.
Payment Requests processing State will be tracked with an Internal State machine of Payment Orchestrator Service.
In fact each Service which processes the Payment Request will have an internal State Machine.
Payment Requests might be forwarded to external Fraud Mitigation Service provider like CyberSource based on the rules.
Payment-UI Service will have a highly functional, secured Payment Page developed with PreactJS framework. 
Payment Requests routing to backend PGSPs or PSPs will be controlled by Rule Engines.
PSP-Gateway Service will act as Gateway to forward the request to the PGSPs or PSPs with the necessary Request-Response adapters.
Card Holder Data will be Encryped with AES-256 algorithm, powered by Google Tink before Storing to database.
CVV will be stored in Non-Persistence Cache powered by Highly Available Valkey Service.
A Job Scheduler Service will host all the Jobs like Storefront-Billing, Settlement, Capture-Initiation, Reconciliation. Job Scheduler will use Quartz Engine to schedule Jobs on timely , clustered fashion across nodes.
All Communications between Microservices will be mTLS protected.
Rules of the Rule Engine will be maintained in the Administration Service.
Payment Orchestrator, Payment-UI will be exposed via OpenShift Ingress Gateway, based on Istio Technology.
The Services are protected by OAuth2 provided by KeyCloak Service.
All Microservices are Stateless and Containerized with Docker. There will be Redundancy/Replicas for the Microservices with Horizontal Scaling enabled. Microservices will be hosted on OpenShift Container Orchestrator Platform.
Payment Platform will be hosted in 2 Data Centres on OpenShift.
