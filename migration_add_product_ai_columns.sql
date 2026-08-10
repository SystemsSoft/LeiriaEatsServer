-- ══════════════════════════════════════════════════════════════════════════
-- Migração: Adicionar colunas à tabela products para melhorar IA
-- Data: 2026-08-10
-- Descrição: Adiciona colunas que ajudam a IA a fazer melhores recomendações
--            baseadas nas necessidades dos clientes
-- ══════════════════════════════════════════════════════════════════════════

-- Ingredientes e composição
ALTER TABLE products ADD COLUMN IF NOT EXISTS ingredients TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS allergens VARCHAR(500);

-- Tags dietéticas (vegetariano, vegano, sem glúten, etc)
ALTER TABLE products ADD COLUMN IF NOT EXISTS dietary_tags VARCHAR(500);

-- Nível de picância
ALTER TABLE products ADD COLUMN IF NOT EXISTS spice_level VARCHAR(50) DEFAULT 'não picante';

-- Informações de porção
ALTER TABLE products ADD COLUMN IF NOT EXISTS serves_people INTEGER;
ALTER TABLE products ADD COLUMN IF NOT EXISTS portion_size VARCHAR(50);

-- Informações nutricionais
ALTER TABLE products ADD COLUMN IF NOT EXISTS calories INTEGER;

-- Status e popularidade
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_popular BOOLEAN DEFAULT FALSE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_available BOOLEAN DEFAULT TRUE;

-- Tempo de preparo estruturado (em minutos)
ALTER TABLE products ADD COLUMN IF NOT EXISTS preparation_time_minutes INTEGER;

-- Recomendação por horário (café da manhã, almoço, jantar, lanche, sobremesa)
ALTER TABLE products ADD COLUMN IF NOT EXISTS recommended_for VARCHAR(200);

-- Tags adicionais para busca (rápido, leve, gourmet, tradicional, kids)
ALTER TABLE products ADD COLUMN IF NOT EXISTS search_tags VARCHAR(500);

-- ══════════════════════════════════════════════════════════════════════════
-- Exemplos de uso para o restaurante preencher:
-- ══════════════════════════════════════════════════════════════════════════

-- UPDATE products SET
--   ingredients = 'mussarela, tomate, manjericão, azeite',
--   allergens = 'glúten, lactose',
--   dietary_tags = 'vegetariano',
--   spice_level = 'não picante',
--   serves_people = 2,
--   portion_size = 'médio',
--   calories = 800,
--   is_popular = TRUE,
--   is_available = TRUE,
--   preparation_time_minutes = 25,
--   recommended_for = 'almoço, jantar',
--   search_tags = 'italiana, tradicional, rápido'
-- WHERE name = 'Pizza Margherita';

-- ══════════════════════════════════════════════════════════════════════════
-- Verificar colunas adicionadas
-- ══════════════════════════════════════════════════════════════════════════

SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'products'
AND column_name IN (
    'ingredients', 'allergens', 'dietary_tags', 'spice_level',
    'serves_people', 'portion_size', 'calories', 'is_popular',
    'is_available', 'preparation_time_minutes', 'recommended_for', 'search_tags'
)
ORDER BY ordinal_position;

