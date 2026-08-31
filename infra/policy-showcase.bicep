targetScope = 'resourceGroup'

@description('Primary region for the retained Policy showcase resources.')
param location string = resourceGroup().location

@description('Globally unique name for the compliant StorageV2 account.')
param storageAccountName string

@description('Globally unique name for the purge-protected Key Vault.')
param keyVaultName string

@description('Name of the private virtual network.')
param virtualNetworkName string = 'vnet-policy-showcase'

@description('Name of the network security group.')
param networkSecurityGroupName string = 'nsg-policy-showcase'

@description('Name of the private-only network interface.')
param networkInterfaceName string = 'nic-policy-showcase'

@description('Name of the intentionally unprotected SMB share used to demonstrate the audit signal.')
param fileShareName string = 'unprotected-demo'

@description('Mark resources as retained for portal inspection instead of cleanup candidates.')
param retainForInspection bool = true

var lifecycle = retainForInspection ? 'RetainForInspection' : 'CleanupCandidate'

// The assignment's required tag is intentionally absent. In report-only mode
// these resources show the non-compliant state; after promotion, the Modify
// remediation inherits the value from the resource group.
var showcaseTags = {
  Purpose: 'AzurePolicyAutomationShowcase'
  Lifecycle: lifecycle
  CreatedBy: 'AzureEnterprisePolicyBaseline'
  DataClassification: 'NoCustomerData'
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: showcaseTags
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {}
}

// This empty share is deliberately not protected by Azure Backup. It is a
// report-only fixture for audit-file-share-backup-protection, not a backup or
// restore qualification.
resource unprotectedShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    enabledProtocols: 'SMB'
    shareQuota: 5
    accessTier: 'TransactionOptimized'
  }
}

resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: networkSecurityGroupName
  location: location
  tags: showcaseTags
  properties: {
    securityRules: [
      {
        name: 'Allow-Https-Demo'
        properties: {
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 200
          direction: 'Inbound'
        }
      }
    ]
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: virtualNetworkName
  location: location
  tags: showcaseTags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'policy-demo'
        properties: {
          addressPrefix: '10.42.1.0/24'
          networkSecurityGroup: {
            id: networkSecurityGroup.id
          }
        }
      }
    ]
  }
}

resource networkInterface 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: networkInterfaceName
  location: location
  tags: showcaseTags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetwork.name, 'policy-demo')
          }
        }
      }
    ]
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: showcaseTags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    accessPolicies: []
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Disabled'
  }
}

output storageAccountId string = storageAccount.id
output intentionallyUnprotectedFileShareId string = unprotectedShare.id
output networkSecurityGroupId string = networkSecurityGroup.id
output virtualNetworkId string = virtualNetwork.id
output privateNetworkInterfaceId string = networkInterface.id
output keyVaultId string = keyVault.id
