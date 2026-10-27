"""
Starter catalogue of categories and the services artisans can offer, matching the
categories on the website (`/categories`). Seeded by `manage.py seed_catalog`.

Add to this list to grow the catalogue; renaming an entry here creates a new row
(rename existing ones in Django admin instead).
"""

CATALOG = [
    {
        "name": "Fashion And Beauty",
        "description": "Tailors, shoemakers, stylists and beauty professionals.",
        "services": [
            ("Tailoring And Dressmaking", "Sewing, alterations and custom outfits."),
            ("Shoe Making And Repair", "Custom footwear, resoling and shoe repairs."),
            ("Hair Styling", "Braiding, weaving, cutting and styling."),
            ("Barbing", "Haircuts, beard trims and grooming."),
            ("Makeup Artistry", "Makeup for events, shoots and everyday looks."),
            ("Nail Care", "Manicures, pedicures and nail art."),
        ],
    },
    {
        "name": "Export Services",
        "description": "Getting goods packed, cleared and shipped out of the country.",
        "services": [
            ("Freight Forwarding", "Arranging air and sea shipments for export."),
            ("Customs Clearing", "Clearing goods through ports and airports."),
            ("Export Documentation", "Permits, certificates and shipping paperwork."),
            ("Export Packaging", "Packing and labelling goods to export standards."),
        ],
    },
    {
        "name": "Home And Property",
        "description": "Building, repairs and upkeep for homes and offices.",
        "services": [
            ("Plumbing", "Leaks, pipe work, water heaters and bathroom fittings."),
            ("Electrical Installation And Repair", "Wiring, sockets, lighting and fault finding."),
            ("Carpentry", "Furniture, doors, roofing and woodwork."),
            ("Painting", "Interior and exterior painting."),
            ("Interior Decoration", "Room design, furnishing and finishing."),
            ("Building Construction", "Building, renovation and masonry."),
            ("Welding And Fabrication", "Gates, burglary proofs and metal work."),
            ("AC Installation And Repair", "Air conditioner installation, servicing and repair."),
            ("Generator Repair", "Generator servicing and repair."),
            ("Tiling", "Floor and wall tiling."),
            ("Cleaning", "Home, office and post-construction cleaning."),
        ],
    },
    {
        "name": "Logistics And Warehousing",
        "description": "Moving, delivering and storing goods.",
        "services": [
            ("Dispatch Delivery", "Same-day parcel and document delivery."),
            ("Haulage And Transport", "Moving goods by van or truck."),
            ("Container Drayage", "Moving containers between ports and warehouses."),
            ("Loading And Offloading", "Loading and unloading goods and containers."),
            ("Packing And Moving", "Packing and relocating homes and offices."),
            ("Warehousing", "Storing and managing goods."),
        ],
    },
    {
        "name": "Agriculture",
        "description": "Farm work, equipment and produce.",
        "services": [
            ("Farm Management", "Planning and running farm operations."),
            ("Farm Equipment Rental", "Tractors and farm machinery for hire."),
            ("Seed And Fertilizer Supply", "Sourcing seeds, fertilizer and farm inputs."),
            ("Lawn Mowing And Gardening", "Lawn care, landscaping and gardening."),
            ("Produce Delivery", "Moving farm produce to markets and buyers."),
        ],
    },
    {
        "name": "Automotive",
        "description": "Repairs and care for cars and other vehicles.",
        "services": [
            ("Auto Mechanic", "Engine, brake and general vehicle repairs."),
            ("Auto Electrician", "Vehicle wiring, batteries and electrical faults."),
            ("Car Wash And Detailing", "Washing, cleaning and detailing vehicles."),
            ("Vulcanizing", "Tyre repair, changing and balancing."),
            ("Panel Beating And Spraying", "Body work, dent repair and respraying."),
        ],
    },
    {
        "name": "Tech/IT",
        "description": "Software, digital services and device repair.",
        "services": [
            ("Website Development", "Building and maintaining websites."),
            ("Software Development", "Custom apps and software."),
            ("Digital Marketing", "Social media, ads and online marketing."),
            ("Game Development", "Designing and building games."),
            ("IT Consulting", "IT setup, support and advice for businesses."),
            ("Gadget Repair", "Phone, laptop and gadget repairs."),
            ("CCTV And Security Installation", "Cameras, alarms and access control."),
        ],
    },
]
