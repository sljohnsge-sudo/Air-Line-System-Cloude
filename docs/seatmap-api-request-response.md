# Seat Map API — Request & Response (Travelport Air v11)

Sample captured 2026-10-23 for EK654 (DXB→CMB). Tokens/access group masked; long arrays trimmed (`...`).
Full raw response: `backend/logs/travelport/21.seatmap_RS.json`.

## 1. Travelport request

`POST https://api.pp.travelport.net/11/air/search/seat/catalogofferingsancillaries/seatavailabilities`

Headers:
```
authorization: Bearer <TOKEN>
content-type: application/json
accept: application/json
xauth_travelport_accessgroup: <ACCESS_GROUP>
accept-version: 11
content-version: 11
taxbreakdown: true
traceid: TraceID_45588712CE09
```

Body (workbench already holds the priced offer + travelers):
```json
{
    "CatalogOfferingsQuerySeatAvailability": {
        "SeatAvailabilityOfferings": {
            "@type": "SeatAvailabilityOfferingsBuildFromReservationWorkbench",
            "BuildFromReservationWorkbench": {
                "ReservationIdentifier": {
                    "Identifier": {
                        "value": "6541a19e-4510-4f93-bd74-23e7add00397",
                        "authority": "Travelport"
                    }
                },
                "OfferIdentifier": {
                    "Identifier": {
                        "value": "e2240466-55b3-4c8a-a4d6-18c58f6527a2",
                        "authority": "Travelport"
                    }
                }
            }
        }
    }
}
```

## 2. Travelport response (trimmed)

* `ReferenceList[].SeatingChart[]` — one chart per flight: `Cabin.Layout` (row range + column letters with W/A/C position) and `Cabin.Row[].Space[]` (seat `location` + `Characteristic` codes: W window, A aisle, 9 centre, CH chair, E exit row, O/U/H/K/L…).
* `CatalogOfferingsID[]` — one entry per flight: `Flight`, `TravelerIdentifierRef`, and `CatalogOffering[]` with price + `SeatAvailability` status (`Available` / `Reserved`) and the seat numbers.
* Seats not listed as Available/Reserved are treated as Blocked.

```json
{
  "CatalogOfferingsAncillaryListResponse": {
    "@type": "CatalogOfferingsAncillaryListResponse",
    "transactionId": "7c49af7652661782bc1ee2f4451e2bbf",
    "traceId": "TraceID_45588712CE09",
    "CatalogOfferingsID": [
      {
        "@type": "CatalogOfferingsTravelerFlight",
        "id": "travflight_h_2",
        "Identifier": {
          "value": "de90f4db-b143-474e-8f80-4c496b7a9950"
        },
        "CatalogOffering": [
          {
            "@type": "CatalogOffering",
            "id": "ancillaryID_h_4",
            "Identifier": {
              "authority": "Travelport",
              "value": "55766176-574f-4882-8519-d1756ca2d565"
            },
            "ContentSource": "GDS",
            "ProductOptions": [
              {
                "@type": "ProductOptions",
                "Product": [
                  {
                    "@type": "ProductSeatAvailability",
                    "id": "productSeatID_h_4",
                    "SeatAvailability": [
                      {
                        "seatAvailabilityStatus": "Available",
                        "value": [
                          "14A",
                          "14E",
                          "14K",
                          "15D",
                          "..."
                        ]
                      },
                      {
                        "seatAvailabilityStatus": "Reserved",
                        "value": [
                          "14B",
                          "14D",
                          "14F",
                          "14J",
                          "..."
                        ]
                      },
                      {
                        "seatAvailabilityStatus": "NoSeat",
                        "value": [
                          "14C",
                          "14G",
                          "14H",
                          "15C",
                          "..."
                        ]
                      }
                    ],
                    "Brand": {
                      "@type": "Brand",
                      "name": "STANDARD SEAT"
                    },
                    "SeatingChartRef": "seatingChart_h_2"
                  }
                ]
              }
            ],
            "Price": {
              "@type": "PriceDetail",
              "CurrencyCode": {
                "decimalPlace": 0,
                "value": "LKR"
              },
              "Base": 0,
              "TotalPrice": 0
            }
          }
        ],
        "Flight": [
          {
            "@type": "Flight",
            "carrier": "EK",
            "number": "0654",
            "id": "Flight_02",
            "Identifier": {
              "authority": "Travelport",
              "value": "58e9d018-0531-4c1f-990b-8147d2f58f05"
            },
            "Departure": {
              "@type": "DepartureDetail",
              "location": "DXB",
              "date": "2026-10-31",
              "time": "10:05:00"
            },
            "Arrival": {
              "@type": "ArrivalDetail",
              "location": "CMB",
              "date": "2026-10-31",
              "time": "15:55:00"
            }
          }
        ]
      }
    ],
    "Result": {
      "@type": "Result"
    },
    "Identifier": {
      "value": "fd0b5523-7c23-4a81-bf53-ddc1bb5e1373"
    },
    "ReferenceList": [
      {
        "@type": "ReferenceListSeatingChart",
        "SeatingChart": [
          {
            "@type": "SeatingChart",
            "id": "seatingChart_h_2",
            "Cabin": [
              {
                "@type": "Cabin",
                "name": "null",
                "Layout": [
                  {
                    "startRow": 14,
                    "endRow": 16
                  },
                  {
                    "position": [
                      "W"
                    ],
                    "value": "A"
                  },
                  {
                    "position": [
                      "A"
                    ],
                    "value": "B"
                  },
                  {
                    "position": [
                      "A"
                    ],
                    "value": "C"
                  },
                  {
                    "position": [
                      "C"
                    ],
                    "value": "D"
                  },
                  {
                    "position": [
                      "C"
                    ],
                    "value": "E"
                  },
                  {
                    "position": [
                      "A"
                    ],
                    "value": "F"
                  },
                  {
                    "position": [
                      "A"
                    ],
                    "value": "G"
                  },
                  {
                    "position": [
                      "C"
                    ],
                    "value": "H"
                  },
                  {
                    "position": [
                      "C"
                    ],
                    "value": "J"
                  },
                  {
                    "position": [
                      "W"
                    ],
                    "value": "K"
                  }
                ],
                "Row": [
                  {
                    "@type": "Row",
                    "label": "14",
                    "Space": [
                      {
                        "@type": "Space",
                        "location": "A",
                        "Characteristic": [
                          "K",
                          "I",
                          "W"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "B",
                        "Characteristic": [
                          "B",
                          "1",
                          "K",
                          "I",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "C",
                        "Characteristic": [
                          "8",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "D",
                        "Characteristic": [
                          "B",
                          "1",
                          "K",
                          "I",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "E",
                        "Characteristic": [
                          "K",
                          "I",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "F",
                        "Characteristic": [
                          "B",
                          "1",
                          "K",
                          "I",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "G",
                        "Characteristic": [
                          "8",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "H",
                        "Characteristic": [
                          "8",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "J",
                        "Characteristic": [
                          "B",
                          "1",
                          "K",
                          "I",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "K",
                        "Characteristic": [
                          "K",
                          "I",
                          "W"
                        ]
                      }
                    ]
                  },
                  {
                    "@type": "Row",
                    "label": "15",
                    "Space": [
                      {
                        "@type": "Space",
                        "location": "A",
                        "Characteristic": [
                          "W"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "B",
                        "Characteristic": [
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "C",
                        "Characteristic": [
                          "8",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "D",
                        "Characteristic": [
                          "H",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "E",
                        "Characteristic": [
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "F",
                        "Characteristic": [
                          "H",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "G",
                        "Characteristic": [
                          "8",
                          "A"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "H",
                        "Characteristic": [
                          "8",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "J",
                        "Characteristic": [
                          "H",
                          "9"
                        ]
                      },
                      {
                        "@type": "Space",
                        "location": "K",
                        "Characteristic": [
                          "W"
                        ]
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}
```

## 3. Our backend response (parsed, per flight)

Produced by `parse_seat_map_response` (`backend/services/workbench_service.py`); returned by `POST /api/bookings/initiate` as `seat_maps[]`.
```json
{
  "flight_number": "EK654",
  "departure_airport": "DXB", "arrival_airport": "CMB",
  "start_row": 14, "end_row": 16,
  "columns": [{"value":"A","position":"Window"},{"value":"B","position":"Aisle"},{"value":"C","position":"Aisle"},
              {"value":"D","position":"Center"},{"value":"E","position":"Center"},{"value":"F","position":"Aisle"},
              {"value":"G","position":"Aisle"},{"value":"H","position":"Center"},{"value":"J","position":"Center"},{"value":"K","position":"Window"}],
  "rows": [{"row_number": 14, "exit_row": false,
            "seats": [{"col":"A","seat_number":"14A","type":"STANDARD SEAT","status":"available","price":0.0,"currency":"LKR"}, "..."]}],
  "available_seat_count": 0,
  "seat_selection_available": true
}
```
`status`: `available` | `occupied` | `blocked` | `empty` (no seat in that slot).
