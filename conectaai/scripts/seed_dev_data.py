# Arquivo: conectaai/scripts/seed_dev_data.py
#
# Seed de dados sintéticos (fictícios, sem nenhum dado pessoal real — ver
# instruções de LGPD do projeto) para desenvolvimento local do módulo
# ConectaAI. Roda com: python -m conectaai.scripts.seed_dev_data
from conectaai.core.database import Base, SessionLocal, engine
from conectaai.core.security import hash_password
from conectaai.models import sql_models  # noqa: F401
from conectaai.models.sql_models import CompanyDB, CreatorDB, OpportunityDB, UserDB

Base.metadata.create_all(bind=engine)

CREATORS = [
    dict(
        name="João Martins", username="@joaomartins", city="São Paulo",
        bio="Criador de conteúdo fitness e lifestyle.",
        categories=["Fitness", "Lifestyle"], followers=96200, engagement_rate=5.8,
        price_min=800, price_max=2500, platforms=["Instagram", "TikTok"], rating=4.8,
        audience_info={"female_percent": 55, "male_percent": 45, "age_ranges": {"18-24": 40, "25-34": 35},
                        "top_locations": {"São Paulo": 60, "Rio de Janeiro": 15}, "interests": ["Fitness", "Saúde"]},
    ),
    dict(
        name="Ana Silva", username="@anasilva", city="São Paulo",
        bio="Beleza, moda e bem-estar.",
        categories=["Beauty", "Fashion"], followers=128400, engagement_rate=6.4,
        price_min=1200, price_max=3500, platforms=["Instagram", "YouTube"], rating=4.9,
        audience_info={"female_percent": 78, "male_percent": 22, "age_ranges": {"18-24": 50, "25-34": 30},
                        "top_locations": {"São Paulo": 55, "Belo Horizonte": 20}, "interests": ["Beleza", "Moda"]},
    ),
    dict(
        name="Pedro Rocha", username="@pedrorocha", city="Rio de Janeiro",
        bio="Tech reviews e games.",
        categories=["Technology", "Gaming"], followers=145800, engagement_rate=4.9,
        price_min=1500, price_max=4000, platforms=["YouTube", "TikTok"], rating=4.7,
        audience_info={"female_percent": 30, "male_percent": 70, "age_ranges": {"18-24": 45, "25-34": 35},
                        "top_locations": {"Rio de Janeiro": 50, "São Paulo": 25}, "interests": ["Tecnologia", "Games"]},
    ),
]

COMPANIES = [
    dict(name="TechFit Sportswear", segment="Moda esportiva", city="São Paulo",
         website="https://techfit.example.com", instagram="@techfit", size="11-50 funcionários",
         avg_campaign_budget=5000, desired_categories=["Fitness", "Lifestyle"]),
]

OPPORTUNITIES = [
    dict(company_name="TechFit Sportswear", campaign_name="Lançamento coleção verão",
         content_type="Reels", category="Fitness", budget_min=1000, budget_max=3000),
    dict(company_name="Bella Cosméticos", campaign_name="Resenha linha skincare",
         content_type="Stories", category="Beauty", budget_min=800, budget_max=2000),
    dict(company_name="GameZone", campaign_name="Unboxing novo console",
         content_type="UGC", category="Gaming", budget_min=1500, budget_max=4000),
]


def run():
    db = SessionLocal()
    try:
        for c in CREATORS:
            email = f"{c['username'].strip('@')}.teste@example.com"
            user = UserDB(role="creator", name=c["name"], email=email, password_hash=hash_password("SenhaTeste123"))
            db.add(user)
            db.flush()
            db.add(CreatorDB(user_id=user.id, **c))

        for c in COMPANIES:
            email = f"{c['name'].lower().replace(' ', '.')}.teste@example.com"
            user = UserDB(role="company", name=c["name"], email=email, password_hash=hash_password("SenhaTeste123"))
            db.add(user)
            db.flush()
            db.add(CompanyDB(user_id=user.id, **c))

        for o in OPPORTUNITIES:
            db.add(OpportunityDB(company_id=db.query(CompanyDB).first().id, **o))

        db.commit()
        print(f"Seed aplicado: {len(CREATORS)} creators, {len(COMPANIES)} empresas, {len(OPPORTUNITIES)} oportunidades.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
