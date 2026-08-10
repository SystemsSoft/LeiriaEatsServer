#!/usr/bin/env python3
"""
Script para verificar memória disponível e configurar swap
Útil antes de carregar modelos grandes como Phi-3
"""
import os
import sys
import subprocess
import platform


def get_memory_info():
    """Obtém informações sobre memória do sistema"""
    system = platform.system()

    if system == "Darwin":  # macOS
        return get_memory_macos()
    elif system == "Linux":
        return get_memory_linux()
    else:
        return {"error": f"Sistema {system} não suportado"}


def get_memory_macos():
    """Obtém informações de memória no macOS"""
    try:
        # Memória física
        vm_stat = subprocess.check_output(['vm_stat']).decode()
        page_size = int(subprocess.check_output(['pagesize']).decode().strip())

        # Processar vm_stat
        lines = vm_stat.split('\n')
        stats = {}
        for line in lines:
            if ':' in line:
                key, value = line.split(':')
                key = key.strip()
                value = value.strip().rstrip('.')
                if value.isdigit():
                    stats[key] = int(value) * page_size

        free_pages = stats.get('Pages free', 0)
        inactive_pages = stats.get('Pages inactive', 0)
        available_memory = (free_pages + inactive_pages) / (1024**3)  # GB

        # Swap
        swap_info = subprocess.check_output(['sysctl', 'vm.swapusage']).decode()
        # Exemplo: vm.swapusage: total = 2048.00M  used = 100.00M  free = 1948.00M
        swap_parts = swap_info.split()
        swap_total = swap_parts[3]
        swap_free = swap_parts[9]

        return {
            "ram_available_gb": round(available_memory, 2),
            "swap_total": swap_total,
            "swap_free": swap_free,
            "system": "macOS"
        }
    except Exception as e:
        return {"error": str(e), "system": "macOS"}


def get_memory_linux():
    """Obtém informações de memória no Linux"""
    try:
        # Memória disponível
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()

        mem_available = 0
        swap_total = 0
        swap_free = 0

        for line in meminfo.split('\n'):
            if 'MemAvailable:' in line:
                mem_available = int(line.split()[1]) / (1024**2)  # GB
            elif 'SwapTotal:' in line:
                swap_total = int(line.split()[1]) / (1024**2)  # GB
            elif 'SwapFree:' in line:
                swap_free = int(line.split()[1]) / (1024**2)  # GB

        return {
            "ram_available_gb": round(mem_available, 2),
            "swap_total_gb": round(swap_total, 2),
            "swap_free_gb": round(swap_free, 2),
            "system": "Linux"
        }
    except Exception as e:
        return {"error": str(e), "system": "Linux"}


def check_requirements_for_phi3():
    """Verifica se o sistema tem recursos suficientes para Phi-3"""
    info = get_memory_info()

    if "error" in info:
        print(f"❌ Erro ao verificar memória: {info['error']}")
        return False

    print("=" * 60)
    print("🔍 VERIFICAÇÃO DE MEMÓRIA PARA PHI-3")
    print("=" * 60)
    print(f"\n💻 Sistema: {info['system']}")

    # Mostrar informações
    if info['system'] == 'macOS':
        print(f"📊 RAM disponível: {info['ram_available_gb']} GB")
        print(f"💾 Swap total: {info['swap_total']}")
        print(f"💾 Swap livre: {info['swap_free']}")
    else:
        print(f"📊 RAM disponível: {info['ram_available_gb']} GB")
        print(f"💾 Swap total: {info['swap_total_gb']} GB")
        print(f"💾 Swap livre: {info['swap_free_gb']} GB")

    # Requisitos do Phi-3 com quantização 8-bit
    required_ram = 3.0  # GB mínimo recomendado
    recommended_total = 4.0  # GB (RAM + Swap)

    print(f"\n📋 REQUISITOS PHI-3 (com quantização 8-bit):")
    print(f"   • Mínimo: {required_ram} GB RAM disponível")
    print(f"   • Recomendado: {recommended_total} GB (RAM + Swap)")

    # Verificar
    ram_available = info['ram_available_gb']

    print(f"\n🎯 ANÁLISE:")
    if ram_available >= required_ram:
        print(f"   ✅ RAM suficiente ({ram_available} GB >= {required_ram} GB)")
        print(f"   ✅ Sistema pronto para carregar Phi-3!")
        return True
    elif ram_available >= 2.0:
        print(f"   ⚠️  RAM limitada ({ram_available} GB)")
        print(f"   💡 Sistema usará swap durante carregamento")
        print(f"   ⏳ Pode ser mais lento, mas funcionará")
        return True
    else:
        print(f"   ❌ RAM insuficiente ({ram_available} GB < {required_ram} GB)")
        print(f"\n💡 SOLUÇÕES:")
        print(f"   1. Feche programas não essenciais")
        print(f"   2. Reinicie o sistema para liberar cache")
        print(f"   3. Configure mais swap (veja instruções abaixo)")
        return False


def show_swap_instructions():
    """Mostra instruções para configurar swap"""
    system = platform.system()

    print("\n" + "=" * 60)
    print("💾 INSTRUÇÕES PARA CONFIGURAR SWAP")
    print("=" * 60)

    if system == "Darwin":  # macOS
        print("\n📱 macOS:")
        print("   O macOS gerencia swap automaticamente.")
        print("   Para verificar uso atual: sysctl vm.swapusage")
        print("\n   Se necessário, feche aplicativos para liberar memória:")
        print("   • Navegadores (Chrome, Safari, Firefox)")
        print("   • IDEs pesadas (Android Studio, XCode)")
        print("   • Aplicativos de design (Photoshop, Figma)")

    elif system == "Linux":
        print("\n🐧 Linux:")
        print("\n   1. Verificar swap atual:")
        print("      sudo swapon --show")
        print("\n   2. Criar arquivo de swap (4GB):")
        print("      sudo fallocate -l 4G /swapfile")
        print("      sudo chmod 600 /swapfile")
        print("      sudo mkswap /swapfile")
        print("      sudo swapon /swapfile")
        print("\n   3. Tornar permanente (opcional):")
        print("      echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab")
        print("\n   4. Ajustar swappiness (recomendado 60):")
        print("      sudo sysctl vm.swappiness=60")

    print("\n" + "=" * 60)


def main():
    print("\n🤖 Verificador de Requisitos para Phi-3-Mini\n")

    # Verificar memória
    can_run = check_requirements_for_phi3()

    # Mostrar instruções de swap
    if not can_run or "--show-swap" in sys.argv:
        show_swap_instructions()

    print("\n" + "=" * 60)
    print("📚 DOCUMENTAÇÃO ADICIONAL:")
    print("   • IMPLEMENTACAO_PHI3_COMPLETA.md")
    print("   • PHI3_UPGRADE.md")
    print("=" * 60 + "\n")

    return 0 if can_run else 1


if __name__ == "__main__":
    sys.exit(main())

