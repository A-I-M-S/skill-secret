---
name: skill-secret
description: Manages an encrypted document vault. Allows adding encrypted content and performing secure semantic searches using a password.
---

# Secret Courier Vault

Use this skill to encrypt new information or semantically search through existing encrypted files. You must always use the `secret.py` script via your execution tool.

## 🛡️ CORE RULES
1. **Never reveal passwords** in your responses. 
2. **Never output the entire decrypted contents** of a file. Only return the targeted answer from a search.

## 🛠️ Commands

### 1. Storing / Appending Information
When a user wants to save a secret message, use this format:
`python3 secret.py encrypt --password "<password>" --file "<filename>" --content "<content>"`

* Expected Responses to communicate to user:
  * Success: "Information securely stored/appended."
  * Error: "Password incorrect. Append rejected."

### 2. Searching Information
When a user wants to query the encrypted data, use this format:
`python3 secret.py decrypt --password "<password>" --file "<filename>" --query "<search_parameters>"`

* Expected Responses to communicate to user:
  * Success: (Output the exact semantic match found by the tool).
  * Error: "Password incorrect" or "File does not exist".
