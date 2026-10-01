Payment Orchestrator routes to another PGSP or PSP in case of request processing failures defined in the rule engine
with the help of State Machine.
Each Storefront is a Tenant tracked in Storefront Management Service. Each Tenant gets a Short-Code and Merchant-ID
according to the PGSP/PSP their payment requests will be forwarded to.
Secrets and Encryptions Keys will be externalized to Hashicorp Vault in dedicated Namespace.
Stateless Services with Horizontal Scaling should cater to 15-18 TPS.
