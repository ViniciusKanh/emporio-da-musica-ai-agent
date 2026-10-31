def test_follow_up_uses_previous_product(agent):
    agent.ask("Quanto custa o Aurora F20?", session_id="s")
    response = agent.ask("e no pix?", session_id="s")
    assert "Aurora F20" in response.text and "R$ 655,50" in response.text  # 690 * 0.95


def test_follow_up_budget_reuses_category(agent):
    agent.ask("Quais ukuleles vocês têm?", session_id="s")
    response = agent.ask("e até 300?", session_id="s")
    assert "Ipê UK-10" in response.text and "Ipê UK-30" not in response.text


def test_general_question_does_not_reuse_context(agent):
    agent.ask("Quanto custa o Aurora F20?", session_id="s")
    response = agent.ask("Quais promoções estão ativas?", session_id="s")
    assert "Promoções ativas" in response.text and "Vento VD-50" in response.text


def test_order_verification_across_two_messages(agent):
    first = agent.ask("Onde está meu pedido 1001?", session_id="s")
    assert "e-mail" in first.text
    second = agent.ask("cliente.um@example.com", session_id="s")
    assert "#1001" in second.text and "entregue" in second.text


def test_return_request_with_verified_order_is_escalated(agent):
    response = agent.ask("Quero devolver o pedido 1001, email cliente.um@example.com")
    assert "#1001" in response.text and "recebeu" in response.text
    assert response.handoff


def test_sessions_are_isolated(agent):
    agent.ask("pedido 1002 cliente.dois@example.com", session_id="alice")
    response = agent.ask("e quando chega meu pedido?", session_id="bob")
    assert "BRSY0000002BR" not in response.text


def test_out_of_scope_and_greeting(agent):
    assert "out_of_scope" in agent.ask("Qual a previsão do tempo amanhã?").intents
    assert "Empório da Música" in agent.ask("Oi!").text


def test_language_switch(agent):
    assert "Our opening hours" in agent.ask("What are your opening hours?").text
    assert "Nosso horário" in agent.ask("Qual o horário de funcionamento?").text


def test_warranty_overlap_is_escalated_not_denied(original_agent):
    response = original_agent.ask("Meu violão deu defeito depois de 45 dias, ainda tenho garantia?")
    assert "garantia legal de 90 dias" in response.text
    assert "não vou negar seu direito" in response.text and response.handoff
