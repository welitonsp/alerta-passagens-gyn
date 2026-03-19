# Simulador de Escalas PM

Uma aplicação web para calcular valores de serviços extras remunerados com base em escalas diurnas e noturnas, escalas azul e vermelha, e regras de dias da semana.

## 🎯 Funcionalidades

- **Cálculo de Serviços Extras**: Calcula automaticamente o valor de serviços extras remunerados
- **Suporte a Escalas**: Suporta escalas AZUL e VERMELHA
- **Turnos**: Diferencia entre turnos DIURNO (05:01 - 21:59) e NOTURNO (22:00 - 05:00)
- **Dias da Semana**: Aplica as regras corretas de escalas por dia da semana
- **Interface Responsiva**: Funciona em desktop, tablet e mobile
- **Tabela de Referência**: Exibe valores por hora e escalas por dia da semana

## 📊 Regras de Cálculo

### Valores por Hora

| Escala | Turno | Valor/Hora |
|--------|-------|-----------|
| VERMELHA | DIURNO | R$ 36,41 |
| VERMELHA | NOTURNO | R$ 41,38 |
| AZUL | DIURNO | R$ 26,47 |
| AZUL | NOTURNO | R$ 29,80 |

### Escalas por Dia da Semana

- **Domingo** - VERMELHA
- **Segunda** - AZUL
- **Terça** - AZUL
- **Quarta** - AZUL
- **Quinta** - AZUL
- **Sexta** - VERMELHA
- **Sábado** - VERMELHA

## 🚀 Como Usar

### Desenvolvimento Local

1. Instale as dependências:
```bash
cd simulador-escalas-pm
pnpm install
```

2. Inicie o servidor de desenvolvimento:
```bash
pnpm dev
```

3. Abra o navegador em `http://localhost:3000`

### Construir para Produção

```bash
pnpm build
```

## 📦 Stack Tecnológico

- **React 19** - Framework UI
- **TypeScript** - Tipagem estática
- **Tailwind CSS 4** - Estilização
- **shadcn/ui** - Componentes UI
- **Vite** - Build tool

## 🏗️ Estrutura do Projeto

```
simulador-escalas-pm/
├── client/
│   ├── public/          # Arquivos estáticos
│   ├── src/
│   │   ├── components/  # Componentes reutilizáveis
│   │   ├── hooks/       # Custom hooks
│   │   ├── pages/       # Páginas da aplicação
│   │   ├── App.tsx      # Componente raiz
│   │   └── index.css    # Estilos globais
│   └── index.html       # HTML principal
├── shared/
│   └── const.ts         # Constantes compartilhadas
└── package.json
```

## 🔧 Customização

### Alterar Valores de Hora

Edite o arquivo `shared/const.ts`:

```typescript
export const VALORES_HORA = {
  "VERMELHA-DIURNO": 36.41,
  "VERMELHA-NOTURNO": 41.38,
  "AZUL-DIURNO": 26.47,
  "AZUL-NOTURNO": 29.8,
};
```

### Alterar Escalas por Dia

Edite o arquivo `shared/const.ts`:

```typescript
export const DIAS_SEMANA = [
  { dia: 1, nome: "Domingo", escala: "VERMELHA" },
  // ... mais dias
];
```

## 📝 Licença

MIT

## 👨‍💻 Autor

Desenvolvido para cálculo de serviços extras remunerados da Polícia Militar.

---

**Acesso Online**: A aplicação está disponível no GitHub Pages para acesso público sem necessidade de autenticação.
