# 🧠 Guia: Novas Colunas de Produto para IA Inteligente

## 📋 Visão Geral

Foram adicionadas **12 novas colunas** à tabela `products` que permitem à IA fazer recomendações muito mais precisas e entender melhor as necessidades dos clientes.

---

## 🆕 Novas Colunas

### 1. **ingredients** (Texto)
**O que é:** Lista de ingredientes separados por vírgula

**Exemplo:**
```
"mussarela, tomate, manjericão, azeite, massa"
```

**Como a IA usa:**
- Responder perguntas como "tem cebola?"
- Evitar ingredientes que o cliente não gosta
- Sugerir pratos baseados em ingredientes favoritos

---

### 2. **allergens** (Texto)
**O que é:** Lista de alergenos comuns

**Valores comuns:**
- `glúten`
- `lactose`
- `amendoim`
- `frutos do mar`
- `soja`
- `ovo`

**Exemplo:**
```
"glúten, lactose"
```

**Como a IA usa:**
- Cliente: "Não posso comer glúten"
- IA: Filtra automaticamente produtos sem glúten

---

### 3. **dietary_tags** (Texto)
**O que é:** Tags de dietas especiais (separadas por vírgula)

**Valores comuns:**
- `vegetariano`
- `vegano`
- `sem glúten`
- `low carb`
- `keto`
- `sem lactose`
- `orgânico`

**Exemplo:**
```
"vegetariano, sem lactose"
```

**Como a IA usa:**
- Cliente: "Sou vegetariano"
- IA: Mostra apenas opções vegetarianas

---

### 4. **spice_level** (Texto)
**O que é:** Nível de picância

**Valores aceitos:**
- `não picante` (padrão)
- `levemente picante`
- `picante`
- `muito picante`

**Como a IA usa:**
- Cliente: "Não gosto de comida picante"
- IA: Evita produtos com picante

---

### 5. **serves_people** (Número)
**O que é:** Quantas pessoas o prato serve

**Exemplo:**
```
1 = Individual
2 = Para dois
4 = Família
```

**Como a IA usa:**
- Cliente: "Somos 3 pessoas"
- IA: "Recomendo 2 pizzas grandes ou 1 família"

---

### 6. **portion_size** (Texto)
**O que é:** Tamanho da porção

**Valores comuns:**
- `individual`
- `pequeno`
- `médio`
- `grande`
- `família`

**Como a IA usa:**
- Cliente: "Quero algo pequeno"
- IA: Filtra por `portion_size = 'pequeno'`

---

### 7. **calories** (Número)
**O que é:** Calorias aproximadas do prato

**Exemplo:**
```
800  (para uma pizza média)
```

**Como a IA usa:**
- Cliente: "Quero algo leve"
- IA: Prioriza pratos com menos calorias

---

### 8. **is_popular** (Sim/Não)
**O que é:** Marca se o produto é popular/destaque

**Como a IA usa:**
- Cliente: "O que vocês recomendam?"
- IA: Prioriza produtos com `is_popular = true`

---

### 9. **is_available** (Sim/Não)
**O que é:** Se o produto está disponível no momento

**Padrão:** `true`

**Como a IA usa:**
- Não mostra produtos com `is_available = false`
- Restaurante pode desativar temporariamente sem deletar

---

### 10. **preparation_time_minutes** (Número)
**O que é:** Tempo de preparo em minutos

**Exemplo:**
```
15  (para um lanche rápido)
30  (para uma pizza)
45  (para um prato elaborado)
```

**Como a IA usa:**
- Cliente: "Quero algo rápido"
- IA: Filtra por `preparation_time_minutes < 20`

---

### 11. **recommended_for** (Texto)
**O que é:** Para quais refeições é recomendado (separado por vírgula)

**Valores comuns:**
- `café da manhã`
- `almoço`
- `jantar`
- `lanche`
- `sobremesa`

**Exemplo:**
```
"almoço, jantar"
```

**Como a IA usa:**
- Cliente pergunta às 14h: "O que vocês têm?"
- IA: Prioriza produtos com `recommended_for` contendo "almoço"

---

### 12. **search_tags** (Texto)
**O que é:** Tags adicionais para busca (separadas por vírgula)

**Valores comuns:**
- `rápido`
- `leve`
- `gourmet`
- `tradicional`
- `kids` (para crianças)
- `fitness`
- `conforto` (comfort food)

**Exemplo:**
```
"rápido, leve, saudável"
```

**Como a IA usa:**
- Melhora a busca semântica
- Cliente: "Algo saudável" → busca por `fitness` ou `leve`

---

## 📝 Exemplos Práticos de Cadastro

### Exemplo 1: Pizza Margherita
```json
{
  "name": "Pizza Margherita",
  "description": "Pizza clássica italiana com molho de tomate, mussarela e manjericão fresco",
  "price": 35.00,
  "category": "Pizza",
  "ingredients": "massa, molho de tomate, mussarela, manjericão, azeite",
  "allergens": "glúten, lactose",
  "dietary_tags": "vegetariano",
  "spice_level": "não picante",
  "serves_people": 2,
  "portion_size": "médio",
  "calories": 800,
  "is_popular": true,
  "is_available": true,
  "preparation_time_minutes": 25,
  "recommended_for": "almoço, jantar",
  "search_tags": "italiana, tradicional, clássica"
}
```

### Exemplo 2: Salada Caesar Vegana
```json
{
  "name": "Salada Caesar Vegana",
  "description": "Alface romana, croutons, molho caesar vegano e queijo vegano",
  "price": 28.00,
  "category": "Saladas",
  "ingredients": "alface romana, croutons, molho caesar vegano, queijo vegano, tomate cereja",
  "allergens": "glúten",
  "dietary_tags": "vegano, vegetariano, low carb",
  "spice_level": "não picante",
  "serves_people": 1,
  "portion_size": "individual",
  "calories": 350,
  "is_popular": false,
  "is_available": true,
  "preparation_time_minutes": 10,
  "recommended_for": "almoço, jantar, lanche",
  "search_tags": "leve, saudável, fitness, rápido"
}
```

### Exemplo 3: Burrito Picante
```json
{
  "name": "Burrito Mexicano Picante",
  "description": "Tortilla recheada com carne, feijão, queijo e molho picante",
  "price": 22.00,
  "category": "Mexicana",
  "ingredients": "tortilla, carne moída, feijão, queijo, pimentão, jalapeño, molho picante",
  "allergens": "glúten, lactose",
  "dietary_tags": null,
  "spice_level": "muito picante",
  "serves_people": 1,
  "portion_size": "individual",
  "calories": 650,
  "is_popular": true,
  "is_available": true,
  "preparation_time_minutes": 15,
  "recommended_for": "almoço, jantar, lanche",
  "search_tags": "mexicana, rápido, apimentado"
}
```

---

## 🤖 Como a IA Vai Usar Essas Informações

### Cenário 1: Cliente Vegetariano
**Cliente:** "Sou vegetariano, o que você me sugere?"

**IA analisa:**
- `dietary_tags` contém "vegetariano"
- Filtra produtos

**IA responde:**
"Temos ótimas opções vegetarianas:
• Pizza Margherita - R$ 35,00 (serve 2 pessoas)
• Salada Caesar Vegana - R$ 28,00 (leve e rápido)
Qual te interessa mais?"

---

### Cenário 2: Cliente com Pressa
**Cliente:** "Preciso de algo rápido"

**IA analisa:**
- `preparation_time_minutes < 20`
- `search_tags` contém "rápido"

**IA responde:**
"Para algo rápido, recomendo:
• Salada Caesar - 10 min de preparo
• Burrito Mexicano - 15 min de preparo"

---

### Cenário 3: Família
**Cliente:** "Somos 4 pessoas, o que pedimos?"

**IA analisa:**
- `serves_people` >= 4 OU multiple de `serves_people`

**IA responde:**
"Para 4 pessoas, sugiro:
• 2 Pizzas Grandes (cada serve 2)
• 1 Pizza Família (serve 4)
Incluímos bebidas?"

---

### Cenário 4: Cliente não gosta de picante
**Cliente:** "Não gosto de comida picante"

**IA analisa:**
- `spice_level != 'picante'`
- `spice_level != 'muito picante'`

**IA filtra:** Automaticamente remove Burrito Picante das sugestões

---

## 🎯 Benefícios

### Para o Restaurante:
✅ Cadastro mais detalhado dos produtos
✅ Clientes encontram mais facilmente o que procuram
✅ Menos cancelamentos por expectativas erradas
✅ Pode destacar produtos populares

### Para a IA:
✅ Recomendações muito mais precisas
✅ Entende restrições alimentares
✅ Sugere baseado em contexto (horário, pessoas, pressa)
✅ Conversa mais natural e útil

### Para o Cliente:
✅ Encontra exatamente o que precisa
✅ Não vê opções que não pode comer
✅ Respostas mais rápidas e relevantes
✅ Experiência personalizada

---

## 🚀 Como Implementar

### 1. Executar a migração
```bash
python run_migration.py migration_add_product_ai_columns.sql
```

### 2. Atualizar produtos existentes
Use o painel de administração ou API para adicionar as informações

### 3. Treinar equipe
Ensine a equipe do restaurante a preencher esses campos

### 4. Testar IA
Use o chat para testar diferentes cenários

---

## 💡 Dicas de Preenchimento

1. **Seja específico:** "mussarela, tomate" melhor que "queijo, vegetais"
2. **Use vírgulas:** Para separar items em listas
3. **Seja consistente:** Use sempre os mesmos termos
4. **Pense no cliente:** O que ele perguntaria?
5. **Mantenha atualizado:** Marque `is_available = false` quando acabar

---

## 📊 Campos Obrigatórios vs Opcionais

### Obrigatórios (como antes):
- `name`, `description`, `price`, `category`, `restaurant_id`

### Altamente Recomendados:
- `ingredients` - Muito importante para busca
- `dietary_tags` - Essencial para vegetarianos/veganos
- `serves_people` - Ajuda muito em recomendações
- `is_popular` - Destaque seus best-sellers

### Opcionais mas Úteis:
- `allergens` - Importante para segurança
- `spice_level` - Evita surpresas desagradáveis
- `calories` - Para clientes preocupados com saúde
- `search_tags` - Melhora busca

---

## 🔍 Exemplos de Queries que a IA Entenderá Melhor

Antes (sem as novas colunas):
- ❌ "Sou vegano" → IA não sabia filtrar
- ❌ "Algo rápido" → IA não sabia o tempo de preparo
- ❌ "Somos 3 pessoas" → IA não sabia quantidades

Agora (com as novas colunas):
- ✅ "Sou vegano" → Filtra por `dietary_tags`
- ✅ "Algo rápido" → Filtra por `preparation_time_minutes`
- ✅ "Somos 3 pessoas" → Calcula por `serves_people`

---

## 🎉 Conclusão

Essas novas colunas transformam o sistema de IA de uma simples busca em um **consultor inteligente** que realmente entende o que o cliente precisa!

**Próximos passos:**
1. Execute a migração
2. Comece preenchendo produtos populares
3. Teste com clientes reais
4. Ajuste baseado no feedback

🚀 **Boa sorte e boas vendas!**

