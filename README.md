# skill-sccret

## Person A creates the vault:

User: "Agent, save 'The physical keys are hidden under the fake rock in the garden' to vault.enc using password 'KeepItSecret99'"

Agent runs: python3 secret.py encrypt --password "KeepItSecret99" --file "vault.enc" --content "The physical keys are hidden under the fake rock in the garden"

## Person A adds to the vault later:

User: "Agent, append 'Wi-Fi password is Guest2026' to vault.enc using password 'KeepItSecret99'"

Agent runs: python3 secret.py encrypt --password "KeepItSecret99" --file "vault.enc" --content "Wi-Fi password is Guest2026"

## Person B asks a question:

User: "Agent, search vault.enc for 'Where are the keys?' using password 'KeepItSecret99'"

Agent runs: python3 secret.py decrypt --password "KeepItSecret99" --file "vault.enc" --query "Where are the keys?"

Agent returns: "The physical keys are hidden under the fake rock in the garden" (The Wi-Fi password chunk is completely hidden from this session).
