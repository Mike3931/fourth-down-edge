# Forecast integrity

PASS for the three retained-baseline records: baseline SHA256 02fc85576c97d5b4cbc343629fa7bfd93e795c0db3c64a55c62ce1d7078164f2 is unchanged. Baseline creation was October 15 at 4:26:28.390277 p.m. Central, after forecasts at 4:23:45.076370 p.m. and before 7:15 p.m. kickoff. This is retained local evidence from the prior session, not a newly obtained independent timestamping-service attestation.

Adoption verifies the externally retained baseline hash and the complete original SQLite prediction, feature and model row hashes before writing a separate immutable attestation. The attestation records the old evidence time separately from today's actual import time; it does not backdate a new forecast. Model artifact bytes also match their registered SHA256. Exact import times and graph hashes: preservation.json; baseline-attestation.json records adoption.

| Approach | Forecast ID | Model version | Original forecast row SHA256 |
|---|---|---|---|
| naive-homefield-v1 | pros-p-bc9ac10d97679525263f79b42c82c3bbe10d1267145dbd66 | pros-naive-homefield-v1-f46e1de9518f26337ea63d64 | f77b175bbd5ea1961ade1ec0d9d4a29efc751f1cc708228c5aa9d951ae30f4a1 |
| naive-rolling-v1 | pros-p-90a867aec7ea352f5131bef3caf2a62ca791e4f160669be7 | pros-naive-rolling-v1-f46e1de9518f26337ea63d64 | 46a3a00750e24d9dd8e35b0a93411a9e521cdece90c80118c588c676010ce609 |
| team-ratings-v1 | pros-p-452b4b3b74054d2fb28a65528efa865aacb26e54edb8c265 | pros-team-ratings-v1-f46e1de9518f26337ea63d64 | eacddd951af13b45980f9ffd448ea8e68c7652a62ce5eda8aef7c213be59b2a3 |

All 27 game forecasts were inspected, not automatically approved. Three have verified baseline attestations and were evaluated. The other 24 remain intact with BLOCKED / Missing pregame freeze attestation. This restriction also applies to earlier un-attested forecasts for other games; no retrospective freeze is manufactured. Future new forecasts obtain an attestation in their publication transaction.

Original probabilities, scoring distributions, timestamps, model versions and feature hashes are unchanged. Full parameters, feature IDs, uncertainty, source hashes and publication/capture times remain in ../tonight-20261015/SEA-DEN-PREGAME-BASELINE.json. preservation.json compares all prior tables against the backup with zero missing or altered rows.
